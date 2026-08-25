-- ============================================================================
-- 시드 정본 — subjects 13개 · sources (MVP 4곳)
-- 2026-08-25
--
-- subject 이름의 정본은 이 파일이다.
-- 3단계 분류 프롬프트의 subjects 목록과 반드시 같아야 한다 — 어긋나면
-- 게이트가 정상 분류를 전부 id·name 불일치로 강등시킨다.
-- 이름 정의(포함 예·경계)는 프롬프트 문서에 있다. 여기는 명단만 둔다.
-- ============================================================================

INSERT INTO subjects (id, name) VALUES
    (1,  '지속가능성 공시·보고 체계'),
    (2,  '공급망 실사·ESG 데이터 요구'),
    (3,  '탄소국경조정과 무역 규제'),
    (4,  '탄소 산정과 전과정평가'),
    (5,  '탄소크레딧·탄소제거·CCUS'),
    (6,  '저탄소 철강과 산업 소재 전환'),
    (7,  '재생에너지와 기업 전력조달'),
    (8,  'AI와 데이터센터 에너지'),
    (9,  '순환경제와 자원순환'),
    (10, '그린워싱과 ESG 규제 리스크'),
    (11, '기후정책과 국가 전환전략'),
    (12, '지속가능금융과 전환금융'),
    (13, '기후 물리적 리스크와 적응');

-- ----------------------------------------------------------------------------
-- sources — MVP 4곳. RSS 만으로 전문이 오는 소스들이다.
--
-- id 4·5·12·13 은 15곳 인벤토리의 번호를 유지한다. 나머지 11곳은
-- 크롤 어댑터를 만들 때 enabled=0 으로 넣는다 (지금 넣으면 /sources/due 가
-- 돌 수 없는 소스를 반환한다).
--
-- 임팩트온:  can_cite=0. RSS 로 전문이 오지만 독자는 페이월을 본다.
--            본문은 파이프라인 안에서만 쓴다.
--            pubDate 에 오프셋이 없다 → tz_offset 을 안 주면 UTC 컨테이너에서
--            9시간이 조용히 밀린다.
-- 환경미디어: dc:date 에 +09:00 이 있어 tz_offset 불필요.
--            description 하나에 전문이 와서 snippet 과 body 가 같다.
-- ----------------------------------------------------------------------------

INSERT INTO sources
    (id, label, press, kind, url, body_mode, tz_offset, config,
     paywall, tier, can_seed, can_cite, lang, poll_interval_min, enabled,
     empty_streak, created_at)
VALUES
    (4,  '환경미디어',   '환경미디어',   'rss',
     'https://www.ecomedia.co.kr/news/rss.php',      'rss_content', NULL,
     '{"rss":{"content_tag":"description"}}',
     'none', 'primary',   1, 1, 'ko',  720, 1, 0, '2026-08-20T00:00:00Z'),

    (5,  '임팩트온',     '임팩트온',     'rss',
     'https://www.impacton.net/rss/allArticle.xml',  'rss_content', '+09:00',
     '{"rss":{"content_tag":"content"}}',
     'full', 'secondary', 1, 0, 'ko',  240, 1, 0, '2026-08-20T00:00:00Z'),

    (12, 'ESG Today',    'ESG Today',    'rss',
     'https://www.esgtoday.com/feed/',               'rss_content', NULL,
     '{"rss":{"content_tag":"content:encoded"}}',
     'none', 'overseas',  1, 1, 'en',  240, 1, 0, '2026-08-20T00:00:00Z'),

    (13, 'Carbon Brief', 'Carbon Brief', 'rss',
     'https://www.carbonbrief.org/feed/',            'rss_content', NULL,
     '{"rss":{"content_tag":"content:encoded","drop_classes":["g-button","print-share","article-contents"]}}',
     'none', 'overseas',  1, 1, 'en', 1440, 1, 0, '2026-08-20T00:00:00Z');
