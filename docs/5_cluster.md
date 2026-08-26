# 5_cluster — 주간 배치: 이슈 클러스터링과 소재 생성

**5~8단계는 하나의 워크플로이자 하나의 재실행 단위다.**

- 프롬프트 정본 → `prompts/5_cluster/v3/` · `prompts/6_compose/v1/`
- 어느 버전을 쓰는가 → `tuning.yaml` 의 `prompt_versions.cluster` · `compose`
- 결정 이력 → `db/DECISIONS.md`
- 스키마 근거 → `db/SCHEMA.md` 의 `batches` · `topics`

---

## 지금까지와 성격이 다르다

```
1~4단계   기사 단위로 닫힌다 · 도착 순서와 무관 · 매일 02:00
5~8단계   여러 기사를 가로지른다 · 시간창 필요 · 주 1회 월요일 04:00
```

처음 쓰이는 것들이다.

```
batches              as_of · window · state · candidate_snapshot · 집계
소비 마커             articles.batch_id · consume_reason (clustered · stale)
BEGIN IMMEDIATE      배치 끝에 한 트랜잭션으로
열도 신호             topics.hot_* — 코드가 센다
```

---

## 왜 5~8단계를 나누지 않는가

**이슈는 영속 데이터가 아니다.**

```
5단계 issue    같은 배치 안에서 만들어져 6단계가 바로 소비한다
6~8단계 topics 배치의 최종 산출물. 영속
```

`issues` 테이블을 안 만들기로 했다(#50). 워크플로를 둘로 나누면 중간 결과를
넘길 영속 계층이 필요해지고, **결국 안 만들기로 한 그 저장소를 다시
요구하게 된다.** 사람이 5단계 결과를 검토하고 6단계를 실행하는 경계도 없다.

### 재실행 단위

```
6단계 이후 실패해도 5단계 이슈는 저장하지 않는다.
topics 저장과 소비 마커가 완료되지 않았다면
**같은 배치 기준(as_of · window · candidate_snapshot)으로** 5단계부터 다시 계산한다.
```

**같은 `as_of` 를 재사용하는 게 핵심이다.** 재실행하면서 새 시각을 잡으면
후보 집합과 열도가 달라져 #48·#61 의 재현성 목적이 깨진다.

---

## 배치 상태 — running 을 이어받는다

```
POST /batches/start
  running 없음  →  새 batch 생성 · as_of·window 고정
  running 있음  →  **기존 batch 반환**. 같은 as_of 로 재실행

POST /batches/{id}/abort
  running → aborted

POST /batches/{id}/complete
  running → done  (전부 통과했을 때만)
```

의미가 갈린다.

```
running   아직 끝내지 않은 동일 배치
aborted   재현성을 포기하고 폐기하기로 **사람이 명시적으로 결정했다**
done      정상 완료
```

### 자동 만료를 두지 않는다

`created_at + N시간` 같은 TTL 을 넣으면 두 가지가 다시 섞인다.

```
n8n 죽음                              ≠  batch aborted
"이 배치는 버리고 새 기준시각으로 간다"  =  batch aborted
```

**프로세스 장애가 배치를 무효로 만들지 않는다.** 다음 실행이 그 `running`
을 그대로 받아 다시 돌리면 된다.

그리고 N 에 근거가 없다 — 2시간이어도 12시간이어도 되는 값이라 **장애 복구
정책을 숫자로 덮는 셈**이다. `empty_streak`·`mapping_failed` 에서 반복한
그 실수다.

**"월요일마다 옛 배치만 이어받는" 상황은 그 배치가 계속 실패하고 있다는
뜻이다.** N시간 지났다는 이유로 조용히 새 `as_of` 를 만들면 **장애가
가려진다.** 같은 `running` 이 계속 반환되어 실패가 눈에 보이는 편이 낫다.

---

## 후보를 스냅샷한다

```sql
batches.candidate_snapshot
  [{"article_id": 83, "subject_id": 3}, ...]
```

`SCHEMA.md` 에 "정확한 재현이 더 필요해지면 배치 시작 시 후보 id 를
스냅샷한다. 지금은 `collected_at <= as_of` 로 충분하다"고 적어뒀다.
**지금이 그 시점이다** — 요구가 "대체로 같은 후보"에서 **검증 기준값**으로
바뀌었다(아래 `/complete` 계약).

기준값을 `complete` 시점에 다시 계산하면 기준 자체가 움직인다. 그 사이
사람이 기사를 `excluded` 로 내리면 후보가 62→61 이 되고, **"누락 1건"으로
422 가 난다. 원인이 LLM 이 아니라 사람인데 그게 안 보인다.**

```
GET /batches/{id}/candidates
  snapshot 이 NULL  →  현재 조건으로 조회 · 스냅샷 저장 · 반환
  snapshot 이 있음  →  **재조회하지 않고** 저장된 기준으로 반환
```

`subject_id` 도 담는다. 기사 집합만 고정하면 **subject 검증만 현재 DB 값을
보게 되어 그 지점만 흔들린다** — 분석이 subject 전제 아래 이뤄진다는
결정(#51)과 조회·저장 사이 재분류를 막는 결정(#121)의 연장이다.

**다른 값은 안 담는다.** `published_at`·`press` 는 안 바뀌고,
`summary`·`issue_signal` 은 `article_analysis` 가 UNIQUE 로 고정한다.
`sources.tier`·`can_cite` 는 **정책이라 조인한다**(#9) — 열도는 현재 값을 쓴다.

### 후보 조회 조건

```sql
WHERE a.batch_id IS NULL                 -- 미소비 = 후보
  AND a.state = 'active'
  AND a.primary_subject_id IS NOT NULL
  AND n.state = 'done'
  AND n.issue_signal IS NOT NULL         -- 무이슈는 묶을 게 없다
  AND a.collected_at <= :as_of           -- 없으면 재실행이 안 재현된다
  AND a.published_at >= :window_from
  AND a.published_at <  :window_to       -- 미래로 잘못 파싱된 기사를 막는다
```

---

## n = 1 subject 는 코드가 만든다

```
subject 기사 수 = 1   →  LLM 호출 안 함. 코드가 singleton issue 생성
subject 기사 수 ≥ 2   →  LLM 클러스터링
```

**근거는 비용이 아니라 층위다.** 기사가 하나뿐이면 판단할 경우의 수가 없다 —
결과는 반드시 singleton 하나다. **판단이 존재하지 않는 자리는 LLM 에 맡기지
않는다.** "셀 수 있는 건 코드가 센다"의 연장이다.

이 기준이면 소스가 늘어 singleton subject 가 사라져도 **분기가 자연스럽게
안 타게 될 뿐** 설계를 다시 볼 일이 없다.

코드가 만들 때 **새 의미를 만들어내지 않는다.**

```
issue_signal   = article_analysis.issue_signal   그대로
article_ids    = [article_id]
cluster_reason = article_analysis.summary        그대로
```

한 기사밖에 없다는 사실 자체가 묶인 이유라, 코드가 문장을 새로 짓거나
LLM 을 한 번 더 부를 이유가 없다.

> 첫 배치 실측: 후보 62건 · subject 13개 · n=1 이 1개(탄소국경조정 1건).
> 클러스터링 콜 13 → 12.

---

## POST /batches/{id}/complete — 전부 아니면 전무

배치 끝에 이것들이 **한 트랜잭션**에 들어간다(#47).

```
topics INSERT              33행쯤
articles UPDATE            batch_id + consume_reason='clustered'
articles UPDATE            창 밖 → 'stale'
batches  UPDATE            state='done' + 집계
```

### 부분 성공을 두지 않는다

`/ingest` 와 성격이 다르다.

```
/ingest      기사 50건이 서로 독립 → 1건 rejected 여도 49건 저장 가능
/complete    topics + 소비 + stale + done 이 합쳐서 하나의 배치 결과
```

소재 하나만 실패했는데 32개를 저장하면 **즉시 답 없는 질문이 생긴다.**

```
실패한 소재의 article_ids 를 소비할까
  소비하면  소재 없는 consumed 기사가 생긴다
  안 하면   부분 완료 배치가 생긴다
batch 를 뭘로 둘까
  done      완전성이 깨진다
  running   재실행이 이미 저장된 32개를 어떻게 다룰지 또 상태가 생긴다
```

**#47 의 장점 — "실패하면 아무것도 안 남고 같은 배치를 그대로 재실행" —
이 통째로 사라진다.**

### 검증 5종

```
1  형식     topic 스키마 · business_relevance 0~1 · article_ids 비어있지 않음
2  소속     모든 article_id 가 candidate_snapshot 에 존재
3  중복     한 article_id 가 둘 이상의 topic 에 등장하면 실패
4  누락     후보 − ⋃ article_ids ≠ ∅ 이면 실패. **자동 보정 없음**
5  subject  topic.subject_id 와 포함 기사들의 스냅샷 subject 가 전부 동일
```

핵심 불변식은 하나다.

```
candidate_snapshot = ⋃ topic.article_ids
각 article_id 는 정확히 한 번만 등장
```

「중복」과 「누락」은 이 한 줄의 양면이다.

### 누락을 코드가 보정하지 않는다

```
n=1 subject 코드 singleton    →  애초에 판단 대상이 없다. **정상 경로**
n≥2 에서 LLM 이 기사 누락      →  판단 과정의 실패. 보정하면 **repair 다**
```

자동 보정하면 **LLM 이 완전 분할을 지켰는지 검증할 수 없게 된다.** 그리고
빠진 기사가 다른 기사와 묶여야 했던 건지 singleton 이어야 했던 건지
코드는 알 수 없다. `issue_signal` 검증 실패를 repair 하지 않기로 한 것과
같은 계열이다.

### 실패 응답

건별 오류 정보는 주되 **건별 부분 저장은 하지 않는다.**

```json
{
  "ok": false,
  "reason": "batch_validation_failed",
  "rejected": [
    {"type": "missing_article",   "article_id": 123},
    {"type": "duplicate_article", "article_id": 456},
    {"type": "subject_mismatch",  "topic_index": 7, "article_id": 789}
  ]
}
```

`/ingest` 에서 얻은 "어디가 깨졌는지 한 번에 본다"는 장점은 가져오면서
배치 원자성은 유지한다.

---

## 열도는 코드가 센다

| 신호 | 누가 |
| --- | --- |
| `hot_n_articles` · `hot_n_press` | 코드 |
| `hot_n_change` | 코드 — `change_type='변화'` 집계 |
| `hot_recent_72h` · `hot_span_hours` | 코드 — `published_at` 분포, **`as_of` 기준** |
| `hot_tier_mix` | 코드 — `sources.tier` |
| `n_citable` | 코드 — `can_cite=1` 기사 수 |
| `business_relevance` | **LLM** — 이것 하나뿐 |

LLM 에게 "이게 얼마나 핫한가"를 물으면 숫자를 지어낸다.

**매체 편향을 조심한다.** 한 매체가 하루 25건을 쏟으면 그 주제가 늘 뜨거워
보인다. 기사 수보다 `DISTINCT press` 가 중요한 신호다.

`final_score = business_relevance` 다. 열도 가산은 없다 — `n_press=2` 가
`+0.1` 만큼 중요하다는 근거가 아직 없고, **점수 가중치는 검증 임계와 달리
순위를 조용히 비튼다**(#38).

---

## 첫 배치에서 볼 것

- **압축률.** 후보 62건 → 이슈 몇 개인가. 스파이크는 38→33 이었다
- **복수 기사 이슈 비율.** 전부 singleton 이면 클러스터링이 일을 안 한 것이고,
  열도 신호(`n_articles`·`n_press`)가 전부 1 로 죽는다
- **`issue_signal` 이 겹치는가.** 4단계에서 66건 전부 signal 이 붙었는데
  같은 흐름끼리 비슷한 표현이 나왔는지가 묶임의 선행 조건이다
- **`business_relevance` 하한.** 스파이크에서 최저 0.42 로 바닥이 안 깔렸다.
  캘리브레이션 종료 조건(`TUNING.md`)의 핵심 항목
- **stale 11건.** Carbon Brief 10 + 환경미디어 1 이 창 밖이다. 백필하지
  않기로 했다 — 창의 존재 이유("오래된 해설이 지금 움직이는 이슈의 열도에
  끼면 안 된다")를 첫 배치부터 깨는 것이라서
