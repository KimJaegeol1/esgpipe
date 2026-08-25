"""collector — esg.db 의 유일한 소유자.

n8n 과 blogstudio 는 HTTP 로만 붙는다. 127.0.0.1 에만 바인딩하므로
외부에서는 도달할 수 없다 (n8n 도 같은 호스트의 프로세스다).
"""
from fastapi import FastAPI, HTTPException, Query

import articles
import classify
import db
import ingest
import prompts
from prompt_validation import SubjectMismatch
import sources
from models import ClassifyIn, IngestIn
import config
from config import get_settings, get_tuning

app = FastAPI(title="esgpipe collector", version="0.1.0")

# 설정 검증은 여기서 한다. 늦게 터지면 systemd 는 active 인데 요청만 실패한다
config.validate()


@app.get("/health")
def health():
    """DB 와 설정이 우리가 아는 모양인지 한 번에 본다."""
    s = get_settings()
    t = get_tuning()
    return {
        "ok": True,
        "db": db.health(),
        "paths": {
            "db": str(s.db_path),
            "tuning": str(s.tuning_path),
        },
        "tuning": {
            "issue_days": t["window"]["issue_days"],
            "batch_cron": t["window"]["batch_cron"],
            "prompt_versions": t["prompt_versions"],
        },
    }


@app.get("/sources/due")
def sources_due():
    """지금 폴링할 소스. 읽기 전용 — last_fetched_at 은 /ingest 가 찍는다."""
    rows = sources.due()
    return {"count": len(rows), "sources": rows}


@app.post("/ingest")
def post_ingest(req: IngestIn):
    """수집 결과 저장. 같은 request_id 는 두 번 처리하지 않는다."""
    try:
        return ingest.handle(req)
    except ingest.Conflict:
        # 저장된 옛 응답을 돌려주면 조용히 틀린 답이 된다
        raise HTTPException(409, {"error": "idempotency_key_reused",
                                  "request_id": req.request_id})
    except ValueError as e:
        raise HTTPException(400, {"error": str(e)})


@app.get("/articles/pending-classify")
def articles_pending_classify(limit: int = Query(500, ge=1, le=500)):
    """3단계 분류 대상. 읽기 전용."""
    rows = articles.pending_classify(limit)
    return {"count": len(rows), "articles": rows}


@app.post("/articles/classify")
def post_classify(req: ClassifyIn):
    """분류 결과 저장. 기사 한 건이 한 요청이다."""
    try:
        return classify.handle(req)
    except classify.Conflict as e:
        raise HTTPException(409, {"error": "result_conflict",
                                  "article_id": req.article_id, "detail": str(e)})
    except classify.Invalid as e:
        raise HTTPException(422, {"error": "invalid_classification",
                                  "article_id": req.article_id, "detail": str(e)})


@app.get("/prompts/{name}")
def get_prompt(name: str):
    """실행 시작에 한 번만 가져간다. 기사마다 가져오면 실행 도중 파일이
    바뀔 때 같은 배치가 서로 다른 프롬프트를 쓴다."""
    try:
        return prompts.load(name)
    except KeyError as e:
        raise HTTPException(404, {"error": "unknown_prompt", "detail": str(e)})
    except SubjectMismatch as e:
        # LLM 호출 전에 막는다. 어긋난 채로 돌면 82콜을 다 쓰고 전부 422 다
        raise HTTPException(500, {"error": "prompt_subject_mismatch",
                                  "detail": str(e)})
    except (FileNotFoundError, OSError) as e:
        raise HTTPException(500, {"error": "prompt_unavailable", "detail": str(e)})

