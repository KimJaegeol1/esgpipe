"""collector — esg.db 의 유일한 소유자.

n8n 과 blogstudio 는 HTTP 로만 붙는다. 127.0.0.1 에만 바인딩하므로
외부에서는 도달할 수 없다 (n8n 도 같은 호스트의 프로세스다).
"""
from fastapi import FastAPI

import db
import sources
from config import get_settings, get_tuning

app = FastAPI(title="esgpipe collector", version="0.1.0")


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
