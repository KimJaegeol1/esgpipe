"""sources 조회 — 스케줄을 DB 가 주도한다.

poll_min 별 트리거를 n8n 에 여러 개 만들지 않는다. due 판정이 여기로
내려오면 소스 on/off 는 enabled 업데이트 한 번이다.
"""
from db import connect

# 읽기 전용이다. last_fetched_at 은 수집이 끝난 뒤 /ingest 가 찍는다 —
# 여기서 찍으면 실패한 수집도 성공처럼 시각이 남아 다음 주기까지 재시도가
# 막히고, empty_streak 이 죽은 피드를 잡는 신호도 흐려진다.
DUE_SQL = """
SELECT id, label, press, kind, url, body_mode, tz_offset, config,
       lang, tier, paywall, can_seed, can_cite,
       poll_interval_min, last_fetched_at, empty_streak
FROM sources
WHERE enabled = 1
  AND (last_fetched_at IS NULL
       OR julianday('now') - julianday(last_fetched_at)
          > poll_interval_min / 1440.0)   -- 1440.0 의 소수점이 중요하다.
                                          -- 정수로 나누면 720/1440=0 이 되어
                                          -- 모든 소스가 항상 due 가 된다
ORDER BY id
"""


def due() -> list[dict]:
    """지금 폴링할 소스. config 는 JSON 문자열 그대로 넘긴다 —
    해석은 어댑터가 자기 네임스페이스만 읽어서 한다."""
    with connect() as c:
        return [dict(r) for r in c.execute(DUE_SQL)]
