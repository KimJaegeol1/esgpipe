"""POST /ingest 요청 모델.

정규화는 n8n 이 한다. collector 는 받은 걸 그대로 믿지 않는다 —
DB 소유자가 검증도 소유한다.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ArticleIn(BaseModel):
    # 필수
    url: str
    url_key: str
    title: str

    # 선택
    guid: str | None = None
    snippet: str | None = None
    body: str | None = None
    published_at: str | None = None
    # published_at 이 NULL 인 이유. n8n 이 안 보내면 collector 는
    # no_date 만 확실하게 판정할 수 있다
    published_at_error: Literal["no_date", "timezone_unknown",
                                "unparsable_date"] | None = None
    lang: Literal["ko", "en"] | None = None
    press: str | None = None          # sources.press 가 정본. 다르면 경고만

    @field_validator("url", "url_key", "title")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("빈 값")
        return v

    @field_validator("snippet", "body", mode="after")
    @classmethod
    def blank_is_none(cls, v: str | None) -> str | None:
        # 공백 문자열은 없는 것으로 본다 — body_state 판정이 여기 걸린다
        return v if v and v.strip() else None


class IngestIn(BaseModel):
    # n8n $execution.id + source_id. 같은 요청을 두 번 처리하지 않기 위한 키
    request_id: str = Field(min_length=1)
    source_id: int
    # 기사가 0건이거나 fetch 실패여도 last_fetched_at 을 찍어야 한다
    fetched_at: str

    # HTTP 성공 AND 피드 파싱 성공. 200 인데 XML 이 깨진 건 false —
    # true + articles=[] 로 보내면 죽은 피드로 오인된다
    fetch_ok: bool
    fetch_error: str | None = None

    articles: list[ArticleIn] = []
