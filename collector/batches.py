"""주간 배치 — 5~8단계는 하나의 재실행 단위다.

POST /batches/start          running 이 있으면 **그걸 반환한다**
GET  /batches/{id}/candidates 후보를 고정한다(candidate_snapshot)
POST /batches/{id}/complete  전부 아니면 전무
POST /batches/{id}/abort     사람이 재현성을 포기하기로 결정했다
"""
import json
import re
from datetime import datetime, timedelta, timezone

from config import get_tuning
from db import connect

AID = re.compile(r'^a(\d+)$')


class Conflict(Exception):
    """배치 상태가 요청과 맞지 않는다"""


class Invalid(Exception):
    """검증 실패. 저장하지 않는다"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 후보 조회 ────────────────────────────────────────────────────────────
# collected_at <= as_of 가 없으면 재실행이 재현되지 않는다 — 배치가 도는
# 사이 늦게 수집된 과거 기사가 후보에 새로 낀다.
# published_at < as_of 상한은 미래로 잘못 파싱된 기사를 막는다.
CANDIDATES_SQL = """
SELECT a.id AS article_id, a.primary_subject_id AS subject_id
FROM articles a
JOIN article_analysis n
     ON n.article_id = a.id AND n.prompt_version = :pv
    AND n.subject_id = a.primary_subject_id
WHERE a.batch_id IS NULL
  AND a.state = 'active'
  AND a.primary_subject_id IS NOT NULL
  AND n.state = 'done'
  AND n.issue_signal IS NOT NULL      -- 무이슈는 묶을 게 없다
  AND a.collected_at <= :as_of
  AND a.published_at >= :wf
  AND a.published_at <  :as_of
ORDER BY a.primary_subject_id, a.published_at DESC, a.id DESC
"""

# 스냅샷에 담긴 id 로 본문 값을 가져온다. 스냅샷이 정본이라 조건을 다시
# 걸지 않는다 — 걸면 기준값이 움직인다.
HYDRATE_SQL = """
SELECT a.id AS article_id, a.primary_subject_id AS cur_subject_id,
       sub.name AS subject_name, s.press, a.published_at, a.title,
       n.summary, n.issue_signal, n.change_type
FROM articles a
JOIN sources  s   ON s.id  = a.source_id
JOIN subjects sub ON sub.id = :sid
JOIN article_analysis n
     ON n.article_id = a.id AND n.prompt_version = :pv AND n.subject_id = :sid
WHERE a.id = :aid
"""


def start() -> dict:
    """running 이 있으면 그걸 반환한다. 없으면 새로 만든다.

    자동 만료(TTL)를 두지 않는다. N 에 근거가 없고, 자동 만료는
    'n8n 죽음'과 'batch 폐기'를 다시 섞는다 — 프로세스 장애가 배치를
    무효로 만들지는 않는다. 계속 같은 running 이 반환되어야 실패가 보인다.
    """
    t = get_tuning()
    now = _now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            prev = c.execute(
                "SELECT * FROM batches WHERE state='running'"
                " ORDER BY id DESC LIMIT 1").fetchone()
            if prev is not None:
                c.execute("ROLLBACK")
                return {**dict(prev), "resumed": True,
                        "candidate_snapshot": None}   # 목록은 /candidates 로

            days = t["window"]["issue_days"]
            as_of = now
            wf = (datetime.now(timezone.utc) - timedelta(days=days)
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur = c.execute(
                "INSERT INTO batches (as_of, window_from, window_to, state, created_at)"
                " VALUES (?,?,?,'running',?)", (as_of, wf, as_of, now))
            bid = cur.lastrowid
            c.execute("COMMIT")
            return {"id": bid, "as_of": as_of, "window_from": wf,
                    "window_to": as_of, "state": "running",
                    "created_at": now, "resumed": False}
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise


def candidates(batch_id: int) -> dict:
    """후보를 고정한다. 스냅샷이 있으면 재조회하지 않는다.

    /complete 의 검증 계약이 '후보 = ⋃ topic.article_ids' 라 후보 집합이
    조회 결과가 아니라 **검증 기준값**이다. complete 시점에 다시 계산하면
    기준 자체가 움직인다.
    """
    t = get_tuning()
    pv = t["prompt_versions"]["analyze"]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            b = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if b is None:
                c.execute("ROLLBACK")
                raise Invalid(f"batch {batch_id} 가 없다")
            if b["state"] != "running":
                c.execute("ROLLBACK")
                raise Conflict(f"batch {batch_id} 는 {b['state']} 다")

            if b["candidate_snapshot"] is None:
                snap = [dict(r) for r in c.execute(
                    CANDIDATES_SQL,
                    {"pv": pv, "as_of": b["as_of"], "wf": b["window_from"]})]
                c.execute("UPDATE batches SET candidate_snapshot=? WHERE id=?",
                          (json.dumps(snap, ensure_ascii=False), batch_id))
                fresh = True
            else:
                snap = json.loads(b["candidate_snapshot"])
                fresh = False

            groups = {}
            for s in snap:
                row = c.execute(HYDRATE_SQL, {
                    "aid": s["article_id"], "sid": s["subject_id"], "pv": pv}).fetchone()
                if row is None:
                    c.execute("ROLLBACK")
                    raise Invalid(
                        f"스냅샷의 article {s['article_id']} (subject {s['subject_id']})"
                        f" 분석을 찾을 수 없다")
                g = groups.setdefault(s["subject_id"], {
                    "subject_id": s["subject_id"],
                    "subject_name": row["subject_name"], "articles": []})
                g["articles"].append({
                    # a98 형태. LLM 이 id 를 산술적으로 다루지 않게 하고,
                    # 형식이 어긋나면 그 자체가 검증 신호다
                    "aid": f"a{row['article_id']}",
                    "press": row["press"],
                    "published_at": row["published_at"],
                    "change_type": row["change_type"],
                    "issue_signal": row["issue_signal"],
                    "title": row["title"],
                    "summary": row["summary"],
                })
            c.execute("COMMIT")
        except (Conflict, Invalid):
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise

    out = sorted(groups.values(), key=lambda g: -len(g["articles"]))
    return {
        "batch_id": batch_id,
        "as_of": b["as_of"],
        "window_from": b["window_from"],
        "window_to": b["window_to"],
        "snapshot_created": fresh,
        "n_articles": len(snap),
        "n_subjects": len(out),
        # 판단이 존재하지 않는 자리는 LLM 에 맡기지 않는다.
        # n=1 이면 결과가 반드시 singleton 하나다
        "n_llm_calls": sum(1 for g in out if len(g["articles"]) >= 2),
        "subjects": out,
    }


def abort(batch_id: int, note: str | None = None) -> dict:
    """사람이 '이 배치는 버리고 새 기준시각으로 간다'고 결정했다.

    프로세스가 죽은 것과 다르다 — 그건 다음 실행이 running 을 이어받는다.
    """
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            b = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if b is None:
                c.execute("ROLLBACK")
                raise Invalid(f"batch {batch_id} 가 없다")
            if b["state"] != "running":
                c.execute("ROLLBACK")
                raise Conflict(f"batch {batch_id} 는 이미 {b['state']} 다")
            c.execute("UPDATE batches SET state='aborted', note=?, finished_at=?"
                      " WHERE id=?", (note, _now(), batch_id))
            c.execute("COMMIT")
        except (Conflict, Invalid):
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return {"batch_id": batch_id, "state": "aborted", "note": note}


def _parse_aid(v):
    """a98 → 98. 형식이 어긋나면 None — 그 자체가 검증 신호다."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        m = AID.match(v.strip())
        if m:
            return int(m.group(1))
    return None


def complete(batch_id: int, topics: list) -> dict:
    """전부 아니면 전무.

    /ingest 와 성격이 다르다. 거기선 기사 50건이 서로 독립이라 1건이
    깨져도 49건이 유효하지만, 여기선 topics·소비 마커·stale·집계가 합쳐서
    하나의 배치 결과다. 소재 하나만 실패했는데 32개를 저장하면 즉시
    답 없는 질문이 생긴다 — 실패한 소재의 기사를 소비할까, batch 를
    done 으로 둘까 running 으로 둘까.
    """
    now = _now()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            b = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if b is None:
                c.execute("ROLLBACK")
                raise Invalid(f"batch {batch_id} 가 없다")
            if b["state"] != "running":
                c.execute("ROLLBACK")
                raise Conflict(f"batch {batch_id} 는 {b['state']} 다")
            if b["candidate_snapshot"] is None:
                c.execute("ROLLBACK")
                raise Conflict("후보를 아직 뽑지 않았다. /candidates 를 먼저 부른다")

            snap = json.loads(b["candidate_snapshot"])
            snap_subject = {s["article_id"]: s["subject_id"] for s in snap}

            # ── 검증 5종. 하나라도 걸리면 DB 쓰기 0 ──────────────────
            rejected = []
            seen = {}                      # article_id → topic_index
            for i, t in enumerate(topics):
                def bad(kind, **kw):
                    rejected.append({"type": kind, "topic_index": i, **kw})

                for k in ("subject_id", "issue_signal", "title", "summary",
                          "article_ids", "business_relevance", "rationale"):
                    if t.get(k) is None:
                        bad("missing_field", field=k)
                br = t.get("business_relevance")
                if not isinstance(br, (int, float)) or not (0 <= br <= 1):
                    bad("business_relevance_out_of_range", value=br)

                ids = t.get("article_ids") or []
                if not isinstance(ids, list) or not ids:
                    bad("empty_article_ids")
                    continue
                for raw in ids:
                    aid = _parse_aid(raw)
                    if aid is None:
                        bad("bad_aid_format", aid=raw)
                        continue
                    if aid not in snap_subject:            # 소속
                        bad("article_not_in_candidates", article_id=aid)
                        continue
                    if aid in seen:                        # 중복
                        rejected.append({"type": "duplicate_article",
                                         "article_id": aid,
                                         "topic_index": i, "first_seen": seen[aid]})
                        continue
                    seen[aid] = i
                    if snap_subject[aid] != t.get("subject_id"):   # subject
                        bad("subject_mismatch", article_id=aid,
                            topic_subject=t.get("subject_id"),
                            article_subject=snap_subject[aid])

            # 누락 — 자동 보정하지 않는다. 보정하면 LLM 이 완전 분할을
            # 지켰는지 검증할 수 없고, 빠진 기사가 다른 기사와 묶여야
            # 했던 건지 singleton 이어야 했던 건지 코드는 알 수 없다
            for aid in snap_subject:
                if aid not in seen:
                    rejected.append({"type": "missing_article", "article_id": aid})

            if rejected:
                c.execute("ROLLBACK")
                return {"ok": False, "reason": "batch_validation_failed",
                        "batch_id": batch_id, "rejected": rejected}

            # ── 전부 통과. 여기서부터 쓰기 ────────────────────────────
            article_ids = sorted(seen)
            n_topics = 0
            for t in topics:
                ids = [_parse_aid(x) for x in t["article_ids"]]
                hot = _heat(c, ids, b["as_of"])
                c.execute(
                    "INSERT INTO topics (batch_id, subject_id, issue_signal, title,"
                    " summary, article_ids, keywords, cluster_reason,"
                    " business_relevance, search_demand, final_score, rationale,"
                    " hot_n_articles, hot_n_press, hot_n_change, hot_recent_72h,"
                    " hot_span_hours, hot_tier_mix, n_citable, state, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,'new',?)",
                    (batch_id, t["subject_id"], t["issue_signal"], t["title"],
                     t["summary"],
                     json.dumps([{"id": i} for i in ids], ensure_ascii=False),
                     json.dumps(t.get("keywords") or [], ensure_ascii=False),
                     t.get("cluster_reason"),
                     t["business_relevance"],
                     # 검색량 공급원이 없다. final_score = business_relevance (#38)
                     t["business_relevance"], t["rationale"],
                     hot["n_articles"], hot["n_press"], hot["n_change"],
                     hot["recent_72h"], hot["span_hours"],
                     json.dumps(hot["tier_mix"], ensure_ascii=False),
                     hot["n_citable"], now))
                n_topics += 1

            c.executemany(
                "UPDATE articles SET batch_id=?, consume_reason='clustered' WHERE id=?",
                [(batch_id, i) for i in article_ids])

            # 창 밖 정리. 조건을 거의 안 본다 — 분류 실패든 무이슈든
            # 시간이 지나면 전부 정리된다. collected_at 상한이 없으면
            # 배치가 도는 동안 늦게 수집된 과거 기사까지 삼킨다
            cur = c.execute(
                "UPDATE articles SET batch_id=?, consume_reason='stale'"
                " WHERE batch_id IS NULL AND collected_at <= ? AND published_at < ?",
                (batch_id, b["as_of"], b["window_from"]))
            n_stale = cur.rowcount

            c.execute(
                "UPDATE batches SET state='done', n_articles=?, n_stale=?,"
                " n_issues=?, n_topics=?, finished_at=? WHERE id=?",
                (len(article_ids), n_stale, n_topics, n_topics, now, batch_id))
            c.execute("COMMIT")
        except (Conflict, Invalid):
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise

    return {"ok": True, "batch_id": batch_id, "state": "done",
            "n_articles": len(article_ids), "n_stale": n_stale,
            "n_topics": n_topics}


def _heat(c, article_ids: list[int], as_of: str) -> dict:
    """열도는 코드가 센다. LLM 에게 물으면 숫자를 지어낸다.

    매체 편향을 조심한다 — 한 매체가 하루 25건을 쏟으면 그 주제가 늘
    뜨거워 보인다. 기사 수보다 DISTINCT press 가 중요한 신호다.
    tier·can_cite 는 정책이라 조인한다(#9). 열도는 현재 값을 쓴다.
    """
    q = ",".join("?" * len(article_ids))
    rows = c.execute(
        f"SELECT a.published_at, a.press, s.tier, s.can_cite, n.change_type"
        f"  FROM articles a"
        f"  JOIN sources s ON s.id = a.source_id"
        f"  LEFT JOIN article_analysis n"
        f"    ON n.article_id = a.id AND n.subject_id = a.primary_subject_id"
        f" WHERE a.id IN ({q})", article_ids).fetchall()

    recent = get_tuning()["window"]["recent_hours"]
    t0 = datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    stamps, tier_mix, press, n_change, n_recent, n_citable = [], {}, set(), 0, 0, 0
    for r in rows:
        press.add(r["press"])
        tier_mix[r["tier"]] = tier_mix.get(r["tier"], 0) + 1
        if r["change_type"] == "변화":
            n_change += 1
        if r["can_cite"]:
            n_citable += 1
        try:
            d = datetime.strptime(r["published_at"], "%Y-%m-%dT%H:%M:%SZ"
                                  ).replace(tzinfo=timezone.utc)
            stamps.append(d)
            if (t0 - d).total_seconds() <= recent * 3600:
                n_recent += 1
        except Exception:
            pass

    span = 0.0
    if len(stamps) >= 2:
        span = (max(stamps) - min(stamps)).total_seconds() / 3600.0
    return {"n_articles": len(rows), "n_press": len(press), "n_change": n_change,
            "recent_72h": n_recent, "span_hours": round(span, 2),
            "tier_mix": tier_mix, "n_citable": n_citable}
