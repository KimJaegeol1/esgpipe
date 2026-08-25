-- ============================================================================
-- 002 · ingest_requests — POST /ingest 멱등성
-- user_version 1 → 2
-- ============================================================================
-- 같은 요청이 두 번 도착하는 경로가 실재한다: 기사는 INSERT OR IGNORE 로
-- 안전하지만 sources 의 헬스 상태(empty_streak·last_ok_at)는 부작용이 남는다.
--
--   첫 요청     10건 삽입 → 응답 유실
--   재시도      10건 전부 duplicated, inserted=0
--   → empty_streak 이 잘못 +1 되어 살아 있는 피드가 죽은 것으로 집계된다
--
-- articles 에 흔적을 남기는 방식은 안 된다 — 빈 피드·전건 rejected·fetch
-- 실패처럼 기사 행이 아예 없는 요청을 기록할 수 없다.
-- sources.last_request_id 하나로도 안 된다 — 그다음 요청 이후에 도착한
-- 재시도를 못 잡는다.

CREATE TABLE ingest_requests (
    request_id    TEXT PRIMARY KEY,          -- n8n $execution.id + source_id
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    -- 같은 request_id 로 다른 내용이 오는 사고를 막는다.
    -- 해시가 다르면 저장된 응답을 돌려주는 게 아니라 409 로 터뜨린다 —
    -- 옛 응답을 돌려주면 조용히 틀린 답이 된다
    payload_hash  TEXT NOT NULL,
    response      TEXT NOT NULL CHECK (json_valid(response)
                                       AND json_type(response) = 'object'),
    created_at    TEXT NOT NULL
);

-- 보존 기간 정리용. 기간 자체는 n8n 의 실제 재시도 범위를 보고 정한다 —
-- 지우는 순간 그 요청의 멱등성도 사라지므로 근거 없이 짧게 잡지 않는다
CREATE INDEX idx_ingest_requests_created ON ingest_requests(created_at);

PRAGMA user_version = 2;
