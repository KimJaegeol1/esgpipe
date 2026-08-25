-- ============================================================================
-- ESG 블로그 파이프라인 — 스키마 정본
-- SQLite · user_version = 1 · 2026-08-25 (rev 3)
--
-- 이 파일이 스키마의 유일한 정본이다. topics 는 **완성된 소재만** 담는다 —
-- 생성 실패분은 저장하지 않으므로 점수·열도가 전부 NOT NULL 이다.
-- 컬럼 정의를 다른 문서에 복사하지 않는다. 근거는 SCHEMA.md, 이력은
-- DECISIONS.md, 조정 가능한 수치는 tuning.yaml 에 있다.
--
-- 적용은 migrations/README.md 를 따른다. 요약하면:
--   PRAGMA journal_mode 는 트랜잭션 밖에서 (WAL 전환은 tx 안에서 불가)
--   나머지 DDL + seed.sql + user_version 은 하나의 트랜잭션으로
-- ============================================================================

-- 이 두 줄은 **반드시 트랜잭션 밖**에서 실행한다.
--   journal_mode  WAL 전환이 트랜잭션 안에서 거부된다
--   foreign_keys  트랜잭션 안에서는 no-op 이라 조용히 0 으로 남는다 (실측)
-- 그리고 foreign_keys 는 연결마다 켜야 한다. 파일에 안 남는다.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1. subjects — 주제 사전
-- ============================================================================
CREATE TABLE subjects (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

-- ============================================================================
-- 2. sources — 수집처
-- ============================================================================
-- 한 행 = 피드 하나.
-- 목표 상태: 소스 추가 = 이 테이블에 행 하나, 코드 변경 0.
CREATE TABLE sources (
    id                 INTEGER PRIMARY KEY,
    label              TEXT    NOT NULL UNIQUE,
    press              TEXT    NOT NULL,

    -- 수집 방법 — 어댑터가 분기하는 공통 값이라 칼럼이다
    kind               TEXT    NOT NULL CHECK (kind IN ('rss','board','api')),
    url                TEXT    NOT NULL,
    body_mode          TEXT    NOT NULL CHECK (body_mode IN ('rss_content','crawl','snippet','api_content')),
    tz_offset          TEXT,

    -- 어댑터 전용 값. 로직이 아니라 값만.
    -- {"rss":{"content_tag":"content:encoded"}, "board":{}, "api":{}, "crawl":{}}
    config             TEXT    CHECK (config IS NULL OR
                                      (json_valid(config) AND json_type(config) = 'object')),

    -- 성격·정책
    paywall            TEXT    NOT NULL DEFAULT 'unknown'
                               CHECK (paywall IN ('none','partial','full','unknown')),
    tier               TEXT    NOT NULL CHECK (tier IN ('primary','secondary','overseas','official','trigger')),
    can_seed           INTEGER NOT NULL DEFAULT 1 CHECK (can_seed IN (0,1)),
    can_cite           INTEGER NOT NULL DEFAULT 1 CHECK (can_cite IN (0,1)),
    lang               TEXT    CHECK (lang IS NULL OR lang IN ('ko','en')),

    -- 스케줄
    -- DEFAULT 를 두지 않는다. 기본값은 tuning.yaml 이 정본이고 collector 가 넣는다 —
    -- DDL 에 720 을 박으면 YAML 만 고쳤을 때 둘이 갈린다
    poll_interval_min  INTEGER NOT NULL CHECK (poll_interval_min > 0),
    enabled            INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),

    -- 헬스체크
    last_fetched_at    TEXT,
    last_ok_at         TEXT,
    last_error         TEXT,
    empty_streak       INTEGER NOT NULL DEFAULT 0 CHECK (empty_streak >= 0),

    created_at         TEXT    NOT NULL
);

-- ============================================================================
-- 3. articles — 기사
-- ============================================================================
CREATE TABLE articles (
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
                                    'subject_mapping_failed',
                                    'no_date','timezone_unknown','unparsable_date',
                                    'manual_service_mismatch','manual_already_covered',
                                    'exclude_keyword','non_analyzable','out_of_domain'
                                )),
    state_note          TEXT,

    -- 주간 배치 소비 마커. NULL 이면 미소비 = 후보
    batch_id            INTEGER REFERENCES batches(id),
    consume_reason      TEXT    CHECK (consume_reason IS NULL OR consume_reason IN ('clustered','stale')),
    -- 되돌리는 경로(requeue·재분할)는 아직 없다. 생기면 ALTER 로 컬럼을 붙인다

    CHECK (published_at IS NOT NULL OR state = 'excluded'),
    CHECK ((batch_id IS NULL) = (consume_reason IS NULL))
);

-- ============================================================================
-- 4. article_analysis — 기사 분석
-- ============================================================================
CREATE TABLE article_analysis (
    id              INTEGER PRIMARY KEY,
    article_id      INTEGER NOT NULL REFERENCES articles(id),
    subject_id      INTEGER NOT NULL REFERENCES subjects(id),   -- 분석 당시 스냅샷

    summary         TEXT,
    issue_signal    TEXT,     -- done 이어도 NULL 정상 (무이슈 / 검증 거절)
    change_type     TEXT    CHECK (change_type IS NULL OR change_type IN ('변화','해설','동향')),

    state           TEXT    NOT NULL CHECK (state IN ('done','failed')),
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    model           TEXT,
    prompt_version  INTEGER NOT NULL CHECK (prompt_version >= 1),
    created_at      TEXT    NOT NULL,

    -- done 이면 요약과 분류가 반드시 있다. 빈 done 이 클러스터링에 흘러가면
    -- 5단계가 아무 내용 없는 기사를 묶으려 든다
    CHECK (state = 'failed' OR (summary IS NOT NULL AND change_type IS NOT NULL)),

    UNIQUE (article_id, prompt_version, subject_id)
);

-- ============================================================================
-- 5. batches — 주간 소재 배치
-- ============================================================================
CREATE TABLE batches (
    id            INTEGER PRIMARY KEY,
    as_of         TEXT    NOT NULL,   -- 배치 시작 UTC 시각. 후보 조회와 열도의 기준
    window_from   TEXT    NOT NULL,
    window_to     TEXT    NOT NULL,
    state         TEXT    NOT NULL CHECK (state IN ('running','done','aborted')),
    n_articles    INTEGER CHECK (n_articles IS NULL OR n_articles >= 0),
    n_stale       INTEGER CHECK (n_stale    IS NULL OR n_stale    >= 0),
    n_issues      INTEGER CHECK (n_issues   IS NULL OR n_issues   >= 0),
    n_topics      INTEGER CHECK (n_topics   IS NULL OR n_topics   >= 0),
    note          TEXT,
    created_at    TEXT    NOT NULL,
    finished_at   TEXT,

    CHECK (window_from < window_to)
);

-- ============================================================================
-- 6. topics — 소재 후보
-- ============================================================================
CREATE TABLE topics (
    id                  INTEGER PRIMARY KEY,
    batch_id            INTEGER NOT NULL REFERENCES batches(id),
    -- 클러스터링이 subject 별로 도니 이슈는 반드시 subject 를 갖는다
    subject_id          INTEGER NOT NULL REFERENCES subjects(id),

    issue_signal        TEXT,        -- 이 소재를 만든 이슈. 기록용
    title               TEXT    NOT NULL,
    summary             TEXT    NOT NULL,

    -- 클러스터 구성 기사 **전체**. can_cite=0 도 여기 들어간다.
    -- 독자에게 보여줄 링크 목록은 sources 조인으로 그때 만든다 (정책이라 소급)
    -- 빈 배열은 소재가 아니다 — 근거 없는 소재를 저장할 이유가 없다
    article_ids         TEXT    NOT NULL CHECK (json_valid(article_ids)
                                                AND json_type(article_ids) = 'array'
                                                AND json_array_length(article_ids) > 0),
    keywords            TEXT    CHECK (keywords IS NULL OR
                                       (json_valid(keywords) AND json_type(keywords) = 'array')),
    cluster_reason      TEXT,

    -- 점수
    business_relevance  REAL    NOT NULL CHECK (business_relevance BETWEEN 0 AND 1),
    search_demand       REAL,        -- 항상 NULL — 검색량 공급원이 없다
    final_score         REAL    NOT NULL CHECK (final_score BETWEEN 0 AND 1),
    rationale           TEXT    NOT NULL,

    -- 열도 신호. 코드가 센다. 합산 공식은 아직 없다 (tuning.yaml)
    -- 코드가 항상 센다. NULL 이면 세다 만 것이다
    hot_n_articles      INTEGER NOT NULL CHECK (hot_n_articles >= 1),
    hot_n_press         INTEGER NOT NULL CHECK (hot_n_press    >= 1),
    hot_n_change        INTEGER NOT NULL CHECK (hot_n_change   >= 0),
    hot_recent_72h      INTEGER NOT NULL CHECK (hot_recent_72h >= 0),
    hot_span_hours      REAL    NOT NULL CHECK (hot_span_hours >= 0),
    hot_tier_mix        TEXT    NOT NULL CHECK (json_valid(hot_tier_mix)
                                                AND json_type(hot_tier_mix) = 'object'),
    n_citable           INTEGER NOT NULL CHECK (n_citable >= 0),

    -- 사람의 판정
    state               TEXT    NOT NULL DEFAULT 'new'
                                CHECK (state IN ('new','kept','rejected','used','stale')),
    -- 어휘는 영문 스네이크: too_broad · no_evidence · service_mismatch ·
    -- already_covered · low_heat. 표시 이름은 화면이 붙인다.
    -- 값 고정은 collector 가 한다 — JSON 배열 원소는 CHECK 에서 json_each 를 못 쓴다
    state_tags          TEXT    CHECK (state_tags IS NULL OR
                                       (json_valid(state_tags) AND json_type(state_tags) = 'array')),
    state_note          TEXT,

    created_at          TEXT    NOT NULL,
    decided_at          TEXT,

    -- 판정이 끝났으면 시각이 있다
    CHECK (state IN ('new','stale') OR decided_at IS NOT NULL)
);

-- ============================================================================
-- 인덱스
-- ============================================================================
CREATE INDEX idx_articles_source_pub    ON articles(source_id, published_at DESC);
CREATE INDEX idx_articles_pool          ON articles(state, primary_subject_id, published_at DESC);
CREATE INDEX idx_articles_body_state    ON articles(body_state);
CREATE INDEX idx_articles_guid          ON articles(guid);
-- 주간 배치 후보 조회. batch_id IS NULL 이 선두여야 한다
CREATE INDEX idx_articles_unconsumed    ON articles(batch_id, collected_at, published_at DESC);

CREATE INDEX idx_analysis_article       ON article_analysis(article_id);

CREATE INDEX idx_topics_list            ON topics(state, final_score DESC);
CREATE INDEX idx_topics_subject         ON topics(subject_id, created_at DESC);
CREATE INDEX idx_topics_batch           ON topics(batch_id);

PRAGMA user_version = 1;
