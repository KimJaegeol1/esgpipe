"""POST /ingest — 수집 결과 저장.

멱등성이 이 모듈의 핵심이다. 기사는 url_key UNIQUE 로 안전하지만
sources 의 헬스 상태는 재시도에 부작용이 남는다.
"""
import hashlib
import json
from datetime import datetime, timezone

from db import connect


class Conflict(Exception):
    """같은 request_id 에 다른 내용. 옛 응답을 돌려주면 조용히 틀린 답이 된다"""


def _hash(payload: dict) -> str:
    # 키 순서가 달라도 같은 내용이면 같은 해시여야 한다
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _body_state(body: str | None, snippet: str | None, body_mode: str) -> str:
    """n8n 이 보낸 값을 믿지 않는다 — 본문이 비었는데 full 이라고 오는 걸 막는다.
    공백 문자열은 모델에서 이미 None 으로 정규화됐다."""
    if body:
        return "full"
    if body_mode == "snippet" and snippet:
        return "snippet_only"
    return "pending"


def handle(req, now: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = req.model_dump()
    h = _hash(payload)

    with connect() as c:
        # DEFERRED 면 첫 쓰기까지 잠금을 안 잡아, 동시 도착한 같은 요청 둘이
        # 나란히 "없음"으로 판정하고 둘 다 진행한다
        c.execute("BEGIN IMMEDIATE")
        try:
            prev = c.execute(
                "SELECT payload_hash, response FROM ingest_requests WHERE request_id = ?",
                (req.request_id,),
            ).fetchone()
            if prev:
                c.execute("ROLLBACK")
                if prev["payload_hash"] != h:
                    raise Conflict(req.request_id)
                return json.loads(prev["response"])

            src = c.execute(
                "SELECT id, press, body_mode FROM sources WHERE id = ?",
                (req.source_id,),
            ).fetchone()
            if src is None:
                c.execute("ROLLBACK")
                raise ValueError(f"source_id {req.source_id} 가 없다")

            resp = _process(c, req, src, now)
            c.execute(
                "INSERT INTO ingest_requests"
                " (request_id, source_id, payload_hash, response, created_at)"
                " VALUES (?,?,?,?,?)",
                (req.request_id, req.source_id, h,
                 json.dumps(resp, ensure_ascii=False), now),
            )
            c.execute("COMMIT")
            return resp
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise


def _process(c, req, src, now) -> dict:
    active_ids, excluded_ids = [], []
    duplicated = 0
    reject_counts: dict[str, int] = {}
    warnings: list[str] = []

    def reject(code: str):
        reject_counts[code] = reject_counts.get(code, 0) + 1

    for a in req.articles:
        # press 는 수집 시점의 사실이고, 그 사실의 출처는 sources 다
        if a.press and a.press != src["press"]:
            warnings.append(f"press 불일치: 받은 '{a.press}' · 저장 '{src['press']}'")

        if a.published_at:
            state, reason = "active", None
        else:
            # 발행시각을 몰라도 저장해야 url_key 중복 차단이 걸린다.
            # 안 하면 매 폴링마다 같은 기사가 재등장한다
            state, reason = "excluded", (a.published_at_error or "no_date")

        cur = c.execute(
            "INSERT OR IGNORE INTO articles"
            " (source_id, press, url, url_key, guid, title, snippet, body,"
            "  body_state, published_at, collected_at, lang, state, state_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (src["id"], src["press"], a.url, a.url_key, a.guid, a.title,
             a.snippet, a.body,
             _body_state(a.body, a.snippet, src["body_mode"]),
             a.published_at, req.fetched_at, a.lang, state, reason),
        )
        if cur.rowcount == 0:
            duplicated += 1
        elif state == "active":
            active_ids.append(cur.lastrowid)
        else:
            excluded_ids.append(cur.lastrowid)

    inserted = len(active_ids) + len(excluded_ids)
    rejected = sum(reject_counts.values())
    streak = _update_source(c, req, src, inserted, rejected, now)

    return {
        "request_id": req.request_id,
        "source_id": src["id"],
        "received": len(req.articles) + rejected,
        "inserted": inserted,
        "active": len(active_ids),
        "active_ids": active_ids,
        "excluded": len(excluded_ids),
        "duplicated": duplicated,
        "rejected": rejected,
        "reject_counts": reject_counts,
        "empty_streak": streak,
        "warnings": warnings,
    }


def _update_source(c, req, src, inserted: int, rejected: int, now: str) -> int:
    """empty_streak 4분기. 원인이 다른 것을 같은 신호로 만들지 않는다."""
    streak = c.execute(
        "SELECT empty_streak FROM sources WHERE id = ?", (src["id"],)
    ).fetchone()["empty_streak"]

    if not req.fetch_ok:
        # 네트워크 장애 며칠을 "죽은 피드"로 오인하면 안 된다 — streak 유지
        c.execute(
            "UPDATE sources SET last_fetched_at = ?, last_error = ? WHERE id = ?",
            (req.fetched_at, req.fetch_error or "fetch_failed", src["id"]),
        )
        return streak

    if inserted > 0:
        # excluded 도 신규다. 발행일이 없어도 새 기사가 왔다는 건
        # 피드가 살아 있다는 뜻이다
        streak = 0
        c.execute(
            "UPDATE sources SET last_fetched_at = ?, last_ok_at = ?,"
            " last_error = NULL, empty_streak = 0 WHERE id = ?",
            (req.fetched_at, now, src["id"]),
        )
    elif rejected > 0 and len(req.articles) == 0:
        # 전건 rejected — n8n 정규화 오류다. 죽은 피드로 오인하면 안 된다
        c.execute(
            "UPDATE sources SET last_fetched_at = ?, last_error = ? WHERE id = ?",
            (req.fetched_at, f"validation_rejected:{rejected}", src["id"]),
        )
    else:
        # 전부 중복이거나 피드 자체가 0건 — 이게 진짜 "새 게 없다"다
        streak += 1
        c.execute(
            "UPDATE sources SET last_fetched_at = ?, empty_streak = ? WHERE id = ?",
            (req.fetched_at, streak, src["id"]),
        )
    return streak
