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

    # ArticleIn 이 아니라 dict 로 받는다. 모델로 받으면 FastAPI 가 요청
    # 파싱 단계에서 검증하고, 한 건만 깨져도 요청 전체가 422 가 된다 —
    # 50건 중 1건 때문에 49건이 같이 버려지고 다음 폴링에서 또 반복된다.
    # 개별 검증은 ingest 가 기사 단위로 하고 rejected 로 센다.
    articles: list[dict] = []


class ClassifyIn(BaseModel):
    """3단계 분류 결과. 기사 한 건이 한 요청이다 —
    묶으면 한 건의 오류를 HTTP 상태로 표현할 수 없다."""
    article_id: int
    # digest 는 게이트가 AI 호출 **전에** 판정한 것이고,
    # subject_id=null 은 AI 가 의도적으로 낸 출력이다. 원인이 다르니 나눈다
    gate_result: Literal["pass", "digest"]
    subject_id: int | None = None
    # 정본은 subjects 테이블이다. 이름은 검증용으로만 받는다 —
    # 불일치 로그가 경계표에 뭘 추가할지 알려주는 재료다
    subject_name: str | None = None
    reason: str | None = None


class AnalyzeIn(BaseModel):
    """4단계 분석 결과. 기사 한 건이 한 요청이다."""
    article_id: int
    # 분석 당시 subject. 조회 시점과 저장 시점 사이에 재분류됐으면
    # 전제가 다르므로 collector 가 막는다
    subject_id: int
    state: Literal["done", "failed"]
    summary: str | None = None
    # 문자열 또는 null. null 은 무이슈라는 정상 출력이다 —
    # 현재 사건에 연결되지 않는 evergreen 기사에 이슈를 강제하면
    # 가짜 해설 클러스터가 생긴다
    issue_signal: str | None = None
    change_type: Literal["변화", "해설", "동향"] | None = None
    error: str | None = None
    attempts: int = Field(default=1, ge=1)
    model: str | None = None
    prompt_version: int = Field(ge=1)


class TopicIn(BaseModel):
    """소재 하나. /batches/{id}/complete 가 배열로 받는다."""
    subject_id: int
    issue_signal: str
    title: str
    summary: str
    # LLM 은 "a98" 형태로 낸다(프롬프트 v3 예시). collector 가 되돌린다 —
    # 형식이 어긋나면 그 자체가 검증 신호다
    article_ids: list[str | int]
    keywords: list[str] = []
    cluster_reason: str | None = None
    business_relevance: float
    rationale: str


class CompleteIn(BaseModel):
    """전부 아니면 전무. 부분 저장하지 않는다."""
    topics: list[TopicIn] = []


class AbortIn(BaseModel):
    note: str | None = None


class TopicPatchIn(BaseModel):
    """소재 판정. 안 보낸 필드는 안 건드린다."""
    state: Literal["new", "kept", "rejected", "used", "stale"] | None = None
    # 어휘는 tuning.yaml 의 topic_reject_tags 가 정본이다
    state_tags: list[str] | None = None
    state_note: str | None = None
