"""articles 조회 — 각 단계가 처리할 기사를 고른다.

조회 조건이 게이트 판정의 절반을 흡수한다. not_active·no_body 를 여기서
거르면 n8n 이 다시 판정할 게 없다 — 같은 판정을 두 곳에 두지 않는다.
"""
from config import get_tuning
from db import connect

# SQLite 의 trim() 은 기본적으로 스페이스(0x20)만 지운다. 줄바꿈·탭은 남는다 —
# body 가 '   \n ' 이면 trim 후 '\n' 이 남아 nullif 를 통과하고, 멀쩡한
# snippet 을 두고 그 한 글자가 이긴다. 지울 문자를 명시해야 한다.
_WS = "' ' || char(10) || char(9) || char(13)"

# 분류 입력은 title + classification_text 다.
#   body 앞부분 우선 → body 가 없으면 snippet → 둘 다 없으면 빈 문자열
#
# snippet 만 쓰면 소스별 정보량이 18배 차이 난다(실측):
#   환경미디어 1,898자(description 에 전문)  ESG Today 106자(대부분 <img>)
# 자르기만 하면 위쪽만 정리되고 아래쪽 빈약함은 그대로다.
#
# 결정 #18("body 는 안 쓴다")의 근거는 "본문 32만 자를 필터에서 또 읽으면
# 비용 2배"였다. 600자 상한이 그 근거를 없앤다 — 정신(싸게 전건, 비싸게
# 통과분만)은 유지되고 구현만 바뀐다. 전체 body 를 LLM 에 보내지 않는다.
_TEXT = (f"coalesce(nullif(trim(a.body, {_WS}), ''), "
         f"nullif(trim(a.snippet, {_WS}), ''), '')")

PENDING_CLASSIFY_SQL = f"""
SELECT a.id, s.press, a.published_at, a.title, a.lang,
       substr({_TEXT}, 1, :n)        AS classification_text,
       length({_TEXT}) > :n          AS text_truncated
FROM articles a
JOIN sources s ON s.id = a.source_id
WHERE a.state = 'active'
  AND a.primary_subject_id IS NULL
  AND a.state_reason IS NULL          -- 사유가 찍힌 기사는 전부 제외한다.
                                      -- mapping_failed 를 매일 재분류하지
                                      -- 않는 게 주 목적이지만, digest·
                                      -- 사람이 뺀 것도 같이 빠진다.
                                      -- subjects 를 늘리면 state_reason 을
                                      -- 지워서 되살린다 — 그게 재분류 경로다
  AND a.body_state IN ('full','snippet_only')
ORDER BY a.published_at DESC, a.id DESC   -- 동률에서 순서가 흔들리면
                                          -- limit 경계의 기사가 배치마다 바뀐다
LIMIT :limit
"""


def pending_classify(limit: int = 500) -> list[dict]:
    n = get_tuning()["classification"]["input_max_chars"]
    with connect() as c:
        rows = [dict(r) for r in c.execute(PENDING_CLASSIFY_SQL,
                                           {"n": n, "limit": limit})]
    # SQLite 에 불린이 없어 비교 결과가 0/1 로 온다. 그대로 내보내면
    # n8n 에서 === true 검사가 실패한다
    for r in rows:
        r["text_truncated"] = bool(r["text_truncated"])
    return rows
