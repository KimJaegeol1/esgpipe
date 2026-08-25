"""설정 — .env 와 tuning.yaml 을 읽는다.

경로·바인딩은 .env, 조정 가능한 수치는 tuning.yaml.
그 둘을 섞지 않는다 — .env 는 이 서버의 사정이고,
tuning.yaml 은 파이프라인의 정책이라 레포에 커밋된다.
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_prefix="ESGPIPE_",
        extra="ignore",
    )

    db_path: Path
    tuning_path: Path
    bind_host: str = "127.0.0.1"
    bind_port: int = 8787


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # 없는 파일을 가리키면 지금 터뜨린다. 앱이 뜬 뒤에 알면 늦다
    if not s.db_path.exists():
        raise RuntimeError(f"DB 가 없다: {s.db_path}")
    if not s.tuning_path.exists():
        raise RuntimeError(f"tuning.yaml 이 없다: {s.tuning_path}")
    return s


@lru_cache
def get_tuning() -> dict:
    """tuning.yaml 을 읽는다. 값의 정본은 이 파일이다."""
    with open(get_settings().tuning_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
