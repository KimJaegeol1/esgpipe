"""topics 조회·판정 — blogstudio 가 읽는 쪽.

지금까지 collector 는 파이프라인이 쓰는 쪽이었다. 여기가 처음으로
읽히는 쪽이고, **#12(rank 비저장)가 여기서 실현된다** — rank 를 저장하면
하나를 제외할 때 나머지가 다 밀린다. 조회할 때 조립한다.
"""
import json
from datetime import datetime, timezone

from config import get_tuning
from db import connect


class Invalid(Exception):
    pass


# rank 는 **배치 전체** 기준이다. state 필터보다 먼저 계산해야
# "3위였는데"를 사람이 기억할 수 있다 — 필터에 따라 순위가 달라지면
# 그 숫자가 의미를 잃는다.
#
# tie-break 가 없으면 같은 점수에서 rank 가 조회마다 흔들린다.
_ORDER = "ORDER BY final_score DESC, id ASC"


def _latest_done_batch(c) -> int | None:
    r = c.execute("SELECT id FROM batches WHERE state='done'"
                  " AND n_topics > 0 ORDER BY id DESC LIMIT 1").fetchone()
    return r["id"] if r else None


def list_topics(batch_id: int | None = None, state: str | None = None,
                limit: int = 100, offset: int = 0) -> dict:
    with connect() as c:
        if batch_id is None:
            batch_id = _latest_done_batch(c)
            if batch_id is None:
                return {"batch_id": None, "count": 0, "total": 0, "topics": []}

        b = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if b is None:
            raise Invalid(f"batch {batch_id} 가 없다")

        # 배치 전체를 순서대로 읽어 rank 를 매긴 뒤 필터한다
        rows = c.execute(
            f"SELECT * FROM topics WHERE batch_id=? {_ORDER}", (batch_id,)).fetchall()
        ranked = [(i + 1, r) for i, r in enumerate(rows)]
        if state:
            ranked = [(k, r) for k, r in ranked if r["state"] == state]
        total = len(ranked)
        page = ranked[offset:offset + limit]

        out = []
        for rank, r in page:
            ids = [x.get("id") for x in json.loads(r["article_ids"])]
            out.append({
                "id": r["id"],
                "rank": rank,
                "subject_id": r["subject_id"],
                "issue_signal": r["issue_signal"],
                "title": r["title"],
                "summary": r["summary"],
                "keywords": json.loads(r["keywords"] or "[]"),
                "rationale": r["rationale"],
                "cluster_reason": r["cluster_reason"],
                "business_relevance": r["business_relevance"],
                # 검색량 공급원이 없다. 항상 NULL — 지어내지 않는다
                "search_demand": r["search_demand"],
                "final_score": r["final_score"],
                # 열도. 코드가 센 값이다
                "hot": {
                    "n_articles": r["hot_n_articles"],
                    "n_press": r["hot_n_press"],
                    "n_change": r["hot_n_change"],
                    "recent_72h": r["hot_recent_72h"],
                    "span_hours": r["hot_span_hours"],
                    "tier_mix": json.loads(r["hot_tier_mix"] or "{}"),
                },
                "n_citable": r["n_citable"],
                # 독자에게 보여줄 링크만. can_cite=0 은 안 나간다 —
                # 열 수 없는 링크를 다는 건 "출처가 있다"를 흉내만 내는 것이다.
                # 정책이라 조인한다(#9): 임팩트온이 유료를 풀면 과거 소재의
                # 근거도 같이 열린다
                "sources": _cite_sources(c, ids),
                "state": r["state"],
                "state_tags": json.loads(r["state_tags"] or "[]"),
                "state_note": r["state_note"],
                "created_at": r["created_at"],
                "decided_at": r["decided_at"],
            })

        return {
            "batch_id": batch_id,
            "batch": {"as_of": b["as_of"], "window_from": b["window_from"],
                      "window_to": b["window_to"], "state": b["state"],
                      "n_articles": b["n_articles"], "n_topics": b["n_topics"]},
            "count": len(out), "total": total,
            "limit": limit, "offset": offset,
            "topics": out,
        }


def _cite_sources(c, article_ids: list[int]) -> list[dict]:
    if not article_ids:
        return []
    q = ",".join("?" * len(article_ids))
    rows = c.execute(
        f"SELECT a.id, a.press, a.title, a.url, a.published_at"
        f"  FROM articles a JOIN sources s ON s.id = a.source_id"
        f" WHERE a.id IN ({q}) AND s.can_cite = 1"
        f" ORDER BY a.published_at DESC", article_ids).fetchall()
    return [{"article_id": r["id"], "press": r["press"], "title": r["title"],
             "url": r["url"], "published_at": r["published_at"]} for r in rows]


_DECIDED = ("kept", "rejected", "used")


def patch_topic(topic_id: int, state: str | None,
                state_tags: list[str] | None,
                state_note: str | None) -> dict:
    """판정을 저장한다.

    **articles 를 건드리지 않는다.** tuning.yaml 의
    topic_reject_tags.articles_action 은 정본으로 유지하되 아직 실행하지
    않는 규칙이다 — 지금 연쇄 처리하면 잘못 rejected 한 순간 기사가
    excluded 되고 되돌릴 경로가 없다(requeue 를 MVP 에서 뺐다).
    판정이 쌓여 분포가 보이면 그때 일괄로 돌린다.
    """
    tags_ok = set(get_tuning().get("topic_reject_tags", {}))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            t = c.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
            if t is None:
                c.execute("ROLLBACK")
                raise Invalid(f"topic {topic_id} 가 없다")

            if state is not None and state not in ("new", "kept", "rejected",
                                                   "used", "stale"):
                c.execute("ROLLBACK")
                raise Invalid(f"state 가 어휘 밖이다: {state!r}")
            if state_tags is not None:
                bad = [x for x in state_tags if x not in tags_ok]
                if bad:
                    c.execute("ROLLBACK")
                    raise Invalid(f"모르는 state_tag: {bad} (아는 것: {sorted(tags_ok)})")

            new_state = state if state is not None else t["state"]
            # decided_at 은 **판정 상태가 바뀔 때만** 찍는다. state_note 만
            # 고쳤다고 갱신하면 "언제 판정했나"가 흐려진다
            decided = t["decided_at"]
            if state is not None and state != t["state"]:
                decided = now if new_state in _DECIDED else None

            c.execute(
                "UPDATE topics SET state=?, state_tags=?, state_note=?, decided_at=?"
                " WHERE id=?",
                (new_state,
                 json.dumps(state_tags, ensure_ascii=False)
                 if state_tags is not None else t["state_tags"],
                 state_note if state_note is not None else t["state_note"],
                 decided, topic_id))
            c.execute("COMMIT")
            r = c.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        except Invalid:
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise

    return {"id": r["id"], "state": r["state"],
            "state_tags": json.loads(r["state_tags"] or "[]"),
            "state_note": r["state_note"], "decided_at": r["decided_at"],
            # 판정 태그가 기사에 어떤 영향을 줄지는 아직 실행하지 않는다.
            # 규칙만 알려준다
            "articles_action": "not_applied"}
