"""POST /articles/classify — 3단계 분류 결과 저장.

멱등성 장치(ingest_requests 같은)는 없다. 기사 한 건이 닫힌 단위라
집계 부작용이 없기 때문이다. 대신 **무조건 UPDATE 하지 않는다** —
같은 결과의 재전송은 안전하지만 다른 결과가 늦게 도착하는 경로가 있다.
pass+subject_id=3 이 저장된 뒤 digest 가 도착해 기사를 excluded 로
내리면 판정이 조용히 뒤집힌다.
"""
from db import connect


class Conflict(Exception):
    """이미 다른 결과가 저장돼 있다"""


class Invalid(Exception):
    """모델 출력이 형식을 어겼다. 저장하지 않는다"""


def handle(req) -> dict:
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            a = c.execute(
                "SELECT id, state, state_reason, primary_subject_id"
                "  FROM articles WHERE id = ?", (req.article_id,)
            ).fetchone()
            if a is None:
                c.execute("ROLLBACK")
                raise Invalid(f"article {req.article_id} 가 없다")

            if req.gate_result == "digest":
                want = ("excluded", "digest", None)
            elif req.subject_id is None:
                # subject_id=null 은 모델의 의도된 출력이다 — subjects 목록의
                # 공백을 찾는 신호라, 형식 오류와 섞으면 그 신호가 죽는다
                want = ("active", "subject_mapping_failed", None)
            else:
                s = c.execute("SELECT id, name FROM subjects WHERE id = ?",
                              (req.subject_id,)).fetchone()
                if s is None:
                    c.execute("ROLLBACK")
                    raise Invalid(f"subject_id {req.subject_id} 가 subjects 에 없다")
                # 이름이 왔으면 정본과 대조한다. 불일치를 mapping_failed 로
                # 강등하지 않는다(결정 #93) — id 와 name 중 모델이 어느 쪽을
                # 의도했는지 알 수 없고, 강등하면 공백 신호가 오염된다
                if req.subject_name and req.subject_name != s["name"]:
                    c.execute("ROLLBACK")
                    raise Invalid(
                        f"subject_name 불일치: 받은 {req.subject_name!r} · "
                        f"정본 {s['name']!r} (id={req.subject_id})")
                want = ("active", None, req.subject_id)

            now = (a["state"], a["state_reason"], a["primary_subject_id"])
            UNCLASSIFIED = ("active", None, None)

            if now == want:
                c.execute("ROLLBACK")
                return _resp(req, "already_applied", want)
            if now != UNCLASSIFIED:
                # 이미 다른 판정이 있다. 늦게 온 결과가 덮어쓰지 않는다
                c.execute("ROLLBACK")
                raise Conflict(f"이미 저장됨: {now} · 받은 것: {want}")

            c.execute(
                "UPDATE articles SET state = ?, state_reason = ?,"
                " primary_subject_id = ? WHERE id = ?",
                (want[0], want[1], want[2], req.article_id))
            c.execute("COMMIT")
            return _resp(req, "applied", want)
        except (Conflict, Invalid):
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise


def _resp(req, result: str, want) -> dict:
    return {
        "article_id": req.article_id,
        "result": result,
        "state": want[0],
        "state_reason": want[1],
        "primary_subject_id": want[2],
        "reason": req.reason,
    }
