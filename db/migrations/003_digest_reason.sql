-- ============================================================================
-- 003 · state_reason 에 digest 추가
-- user_version 2 → 3
-- ============================================================================
-- 다이제스트(【데일리 브리핑】·【ESG Deal】·핫클립)는 서로 무관한 5~20건을
-- 한 기사에 담는다. summary 2~4문장도 issue_signal 하나도 성립하지 않는다.
--
-- 3단계 게이트가 제목 패턴으로 AI 호출 **전에** 걸러내는데, 판정 결과를
-- 저장하지 않으면 다음 조회에 또 나온다. 매 배치마다 같은 기사를 다시
-- 걸러내는 걸 영원히 반복하게 된다.
--
-- non_analyzable 을 재활용하지 않는다 — 그 어휘는 "채용·행사·세미나·신간·
-- 인사·수상"이라 성격이 다르고, 섞으면 집계에서 갈라볼 수 없다.
-- (원인이 다른 것을 같은 신호로 만들지 않는다 — empty_streak 4분기와 같은 계열)
--
-- state 는 excluded 다. 분류 실패가 아니라 **기사 단위로는 분석할 수 없다는
-- 확정 판정**이라, active 로 남기면 이후 모든 조회가 예외 처리를 반복한다.
-- body 는 그대로 남으므로 나중에 다이제스트를 쪼개기로 하면 재료는 보존된다.
--
-- SQLite 는 CHECK 을 수정할 수 없어 테이블 재생성이다. 지금 82행이라 싸다.

-- PRAGMA foreign_keys 는 트랜잭션 안에서 no-op 이다. 이 파일을 실행하는 쪽이
-- 트랜잭션 **밖에서** OFF 로 두고, 끝난 뒤 ON 으로 되돌린 다음
-- foreign_key_check 를 돌려야 한다. migrations/README.md 참조.

CREATE TABLE articles_new (
    id                  INTEGER PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES sources(id),
    press               TEXT    NOT NULL,   -- 수집 시점 매체명 복사 (사실이라 복사)
    primary_subject_id  INTEGER REFERENCES subjects(id),

    url                 TEXT    NOT NULL,
    url_key             TEXT    NOT NULL UNIQUE,
    guid                TEXT,

    title               TEXT    NOT NULL,
    snippet             TEXT,
    body                TEXT,
    body_state          TEXT    NOT NULL DEFAULT 'pending'
                                CHECK (body_state IN ('pending','full','snippet_only','failed')),
    body_error          TEXT,

    published_at        TEXT,
    collected_at        TEXT    NOT NULL,
    lang                TEXT    CHECK (lang IS NULL OR lang IN ('ko','en')),

    state               TEXT    NOT NULL DEFAULT 'active'
                                CHECK (state IN ('active','excluded')),
    state_reason        TEXT    CHECK (state_reason IS NULL OR state_reason IN (
                                    -- 3단계
                                    'subject_mapping_failed',
                                    'digest',
                                    -- 1단계 날짜 사유
                                    'no_date','timezone_unknown','unparsable_date',
                                    -- 사람이 뺀 것 (자유 서술은 state_note 에)
                                    'manual_service_mismatch','manual_already_covered',
                                    -- MVP 보류 — 켜질 때를 위해 어휘만 확보
                                    'exclude_keyword','non_analyzable','out_of_domain'
                                )),
    state_note          TEXT,

    -- 주간 배치 소비 마커. NULL 이면 미소비 = 후보
    batch_id            INTEGER REFERENCES batches(id),
    consume_reason      TEXT    CHECK (consume_reason IS NULL OR consume_reason IN ('clustered','stale')),
    -- 되돌리는 경로(requeue·재분할)는 아직 없다. 생기면 ALTER 로 컬럼을 붙인다

    CHECK (published_at IS NOT NULL OR state = 'excluded'),
    CHECK ((batch_id IS NULL) = (consume_reason IS NULL)),
    -- digest 는 확정 판정이라 active 로 남을 수 없다. 어휘만 추가하고
    -- 조합을 안 막으면 active + digest 가 조용히 생긴다
    CHECK (state_reason != 'digest' OR state = 'excluded')
);

INSERT INTO articles_new
SELECT id, source_id, press, primary_subject_id,
       url, url_key, guid,
       title, snippet, body, body_state, body_error,
       published_at, collected_at, lang,
       state, state_reason, state_note,
       batch_id, consume_reason
  FROM articles;

DROP TABLE articles;
ALTER TABLE articles_new RENAME TO articles;

CREATE INDEX idx_articles_source_pub    ON articles(source_id, published_at DESC);
CREATE INDEX idx_articles_pool          ON articles(state, primary_subject_id, published_at DESC);
CREATE INDEX idx_articles_body_state    ON articles(body_state);
CREATE INDEX idx_articles_guid          ON articles(guid);
CREATE INDEX idx_articles_unconsumed    ON articles(batch_id, collected_at, published_at DESC);

PRAGMA user_version = 3;
