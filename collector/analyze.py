"""POST /articles/analyze — 4단계 분석 결과 저장.

3단계(classify)와 성격이 다르다. 분류는 articles 를 UPDATE 하지만
분석은 article_analysis 에 **행을 추가**한다 — 프롬프트를 고치면 다시
만들고 옛 버전을 지우지 않는다. "프롬프트를 고쳤더니 어떻게 달라졌나"가
유일한 튜닝 재료다.

성공한 분석은 UNIQUE (article_id, prompt_version, subject_id) 가 지킨다.
같은 셋이면 안 넣는다.

**실패 행만 예외다.** INSERT OR IGNORE 로 두면 재시도가 조용히 무시되고
failed 행이 그대로 남아 다음 배치에서 또 잡힌다 — 영원히 반복된다.
attempts 도 안 오른다(n8n 은 항상 1 을 보낸다). 그러면 attempts 컬럼과
"failed AND attempts < 3" 조회 조건이 둘 다 죽는다.

실패는 옛 버전을 보존할 이유가 없다. "프롬프트를 고쳤더니 어떻게
달라졌나"의 재료가 되는 건 성공한 분석이고, 실패 행은 재시도 카운터일 뿐이다.
attempts 는 요청 값이 아니라 **collector 가 센다.**
"""
from datetime import datetime, timezone

from config import get_tuning
from db import connect


class Invalid(Exception):
    """형식이 어긋났다. 저장하지 않는다"""


def handle(req) -> dict:
    t = get_tuning()["validation"]
    sig_max = t["issue_signal_max_chars"]
    sum_warn = t["summary_warn_chars"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            a = c.execute(
                "SELECT id, state, primary_subject_id FROM articles WHERE id = ?",
                (req.article_id,)).fetchone()
            if a is None:
                c.execute("ROLLBACK")
                raise Invalid(f"article {req.article_id} 가 없다")
            if a["primary_subject_id"] is None:
                c.execute("ROLLBACK")
                raise Invalid(f"article {req.article_id} 은 아직 분류되지 않았다")
            # 분석은 subject 전제 아래 이뤄진다. n8n 이 조회 시점의 subject 로
            # 프롬프트를 만들었는데 그 사이 재분류됐다면 전제가 다르다
            if req.subject_id != a["primary_subject_id"]:
                c.execute("ROLLBACK")
                raise Invalid(
                    f"subject 가 바뀌었다: 받은 {req.subject_id} · 현재 "
                    f"{a['primary_subject_id']}")

            warnings = []
            signal = req.issue_signal
            signal_rejected = None

            if req.state == "done":
                if not req.summary or not req.summary.strip():
                    c.execute("ROLLBACK")
                    raise Invalid("done 인데 summary 가 비었다")
                if req.change_type not in ("변화", "해설", "동향"):
                    c.execute("ROLLBACK")
                    raise Invalid(f"change_type 이 3종 밖이다: {req.change_type!r}")

                # 길이는 공백 포함, 양끝만 trim 후 센다
                if signal is not None:
                    s = signal.strip()
                    if not s:
                        signal, signal_rejected = None, "empty"
                    elif len(s) > sig_max:
                        # repair 하지 않는다. 고쳐 넣으면 "검증기가 통과시킨
                        # 값"과 "검증기가 만들어낸 값"이 섞여 클러스터링
                        # 품질을 추적할 수 없다
                        signal, signal_rejected = None, f"too_long:{len(s)}"
                    else:
                        signal = s

                if len(req.summary.strip()) > sum_warn:
                    # 경고만. 데이터는 안 건드린다
                    warnings.append(f"summary {len(req.summary.strip())}자 "
                                    f"(경고 임계 {sum_warn})")

            prev = c.execute(
                "SELECT id, state, attempts FROM article_analysis"
                " WHERE article_id = ? AND prompt_version = ? AND subject_id = ?",
                (req.article_id, req.prompt_version, req.subject_id)).fetchone()

            if prev is not None and prev["state"] == "failed":
                # 재시도. attempts 는 collector 가 센다
                attempts = prev["attempts"] + 1
                c.execute(
                    "UPDATE article_analysis SET summary = ?, issue_signal = ?,"
                    " change_type = ?, state = ?, error = ?, attempts = ?,"
                    " model = ?, created_at = ? WHERE id = ?",
                    ((req.summary or "").strip() or None, signal, req.change_type,
                     req.state, req.error, attempts, req.model, now, prev["id"]))
                c.execute("COMMIT")
                return _resp(req, "retried", signal, signal_rejected, warnings,
                             attempts)

            cur = c.execute(
                "INSERT OR IGNORE INTO article_analysis"
                " (article_id, subject_id, summary, issue_signal, change_type,"
                "  state, error, attempts, model, prompt_version, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (req.article_id, req.subject_id,
                 (req.summary or "").strip() or None,
                 signal, req.change_type,
                 req.state, req.error, req.attempts, req.model,
                 req.prompt_version, now))
            inserted = cur.rowcount > 0
            c.execute("COMMIT")
            return _resp(req, "inserted" if inserted else "already_exists",
                         signal, signal_rejected, warnings, req.attempts)
        except Invalid:
            raise
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise


def _resp(req, result, signal, signal_rejected, warnings, attempts) -> dict:
    return {
        "article_id": req.article_id,
        "subject_id": req.subject_id,
        "result": result,
        "state": req.state,
        "attempts": attempts,
        # issue_signal 이 NULL 인 이유는 셋이다 — 무이슈(정상 출력) ·
        # 검증 거절 · 분석 실패. 원인이 다른 것을 같은 신호로 만들지 않는다
        "issue_signal": signal,
        "signal_rejected": signal_rejected,
        "no_signal": req.state == "done" and signal is None and signal_rejected is None,
        "summary_chars": len((req.summary or "").strip()),
        "warnings": warnings,
    }
