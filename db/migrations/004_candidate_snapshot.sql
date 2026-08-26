-- ============================================================================
-- 004 · batches.candidate_snapshot — 배치 후보를 고정한다
-- user_version 3 → 4
-- ============================================================================
-- /complete 의 검증 계약이 이 불변식이다:
--
--     후보 집합 = ⋃ topic.article_ids
--     각 article_id 는 정확히 한 번만 등장
--
-- **이 순간부터 후보 집합은 조회 결과가 아니라 검증 기준값이다.**
-- 기준값을 complete 시점에 다시 계산하면 기준 자체가 움직인다 — 그 사이
-- 사람이 기사를 excluded 로 내리거나 재분류하면 후보가 62건에서 61건으로
-- 줄고, "누락 1건"으로 422 가 난다. 원인이 LLM 이 아니라 사람인데 안 보인다.
--
-- SCHEMA.md 에 "정확한 재현이 더 필요해지면 배치 시작 시 후보 id 를
-- 스냅샷한다. 지금은 collected_at <= as_of 로 충분하다"고 적어뒀다.
-- 지금이 그 시점이다 — 요구가 "대체로 같은 후보"에서 "검증 기준"으로 바뀌었다.
--
-- subject_id 도 같이 담는다. 기사 집합만 고정하면 subject 검증만 현재
-- DB 값을 보게 되어 그 지점만 흔들린다. 분석이 subject 전제 아래 이뤄진다는
-- 결정(#51)과 조회·저장 사이 재분류를 막는 결정(#121)의 연장이다.
--
-- 다른 값은 안 담는다:
--   published_at · press          안 바뀐다
--   summary · issue_signal        article_analysis 가 UNIQUE 로 고정한다
--   sources.tier · can_cite       **정책이라 조인한다**(#9). 열도는 현재 값

ALTER TABLE batches ADD COLUMN candidate_snapshot TEXT
    CHECK (candidate_snapshot IS NULL
           OR (json_valid(candidate_snapshot)
               AND json_type(candidate_snapshot) = 'array'));

-- NULL 이면 아직 후보를 안 뽑았다는 뜻이다.
-- GET /batches/{id}/candidates 가 처음 호출될 때 채우고, 그다음부터는
-- **재조회하지 않고** 저장된 스냅샷 기준으로 반환한다.
--   [{"article_id": 83, "subject_id": 3}, ...]

PRAGMA user_version = 4;
