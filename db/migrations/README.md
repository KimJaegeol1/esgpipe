# migrations/

## 규칙

```
schema.sql        현재 전체 상태. 새 DB 는 이것으로만 만든다
seed.sql          현재 시드. 새 DB 는 이것으로만 채운다
migrations/NNN    이미 존재하는 DB 를 다음 버전으로 올리는 델타
```

**최초 스키마에 해당하는 마이그레이션 파일을 두지 않는다.**

한때 `001_init.sql` 에 `PRAGMA user_version = 1` 만 넣고 "내용은 schema.sql
이다"라고 주석으로 미룬 적이 있다. DDL 중복을 피하려던 것인데, **그 파일만
적용하면 테이블이 0개인 빈 DB 가 정상적인 버전 1 로 보인다.** 마이그레이션
러너가 그걸 집으면 조용히 망가진 DB 가 만들어진다. 그래서 파일을 없앴다.

델타는 `002` 부터 시작한다. 델타를 쓸 때는 **같은 커밋에서 `schema.sql` 도
최신 상태로 고친다.** 둘 중 하나만 고치면 새 DB 와 기존 DB 가 갈라진다.

## 새 DB 만들기

**PRAGMA 두 개가 트랜잭션 안에서 안 먹는다.** 둘 다 실측으로 확인했다.

```
journal_mode = WAL    트랜잭션 안에서 전환이 거부된다 (에러)
foreign_keys = ON     트랜잭션 안에서는 no-op — 에러 없이 조용히 0 으로 남는다
```

두 번째가 더 위험하다. **에러가 안 나므로 FK 가 꺼진 채로 초기화가 끝난다.**
그래서 순서가 이렇다.

```
1. PRAGMA journal_mode = WAL     트랜잭션 밖. 파일에 남는다
2. PRAGMA foreign_keys  = ON     트랜잭션 밖. 파일에 안 남는다 — 연결마다 켠다
3. BEGIN                         ── 여기부터 하나의 트랜잭션
     schema.sql 의 DDL·인덱스     (위 두 PRAGMA 줄은 빼고)
     seed.sql                    subjects · sources
     PRAGMA user_version = 1
   COMMIT
```

`schema.sql` 맨 위의 PRAGMA 두 줄은 **트랜잭션 밖에서 먼저 실행하고, 트랜잭션
안에서는 건너뛴다.** "schema.sql 의 나머지를 BEGIN 안에서"라고만 쓰면 그 두
줄이 안쪽으로 들어갈 여지가 있다.

**스키마와 시드를 한 트랜잭션으로 묶는 것이 핵심이다.** 나누면 시드가 실패해도
`user_version = 1` 인 채로 남아, subjects 가 비어 있는 DB 를 "정상 v1" 으로
착각한다.

이 초기화는 collector 가 수행한다. 손으로 돌릴 일이 생기면 파일 순서만
맞추면 되지만, 트랜잭션 보장은 없다는 걸 알고 해야 한다.

> **함정: 파이썬 `sqlite3.executescript` 는 실행 전에 암묵적으로 COMMIT 한다.**
> `BEGIN` 을 걸고 `executescript` 를 부르면 트랜잭션이 그 자리에서 끝나고,
> 뒤이은 `COMMIT` 은 `cannot commit - no transaction is active` 로 터진다.
> 초기화는 **문장을 하나씩 `execute`** 해야 한다. 실제로 이걸로 한 번 막혔다.

**검증된 동작**: 시드 중간에 실패시켜 보면 롤백 후 테이블 0개 ·
`user_version = 0` 이 된다. 반쯤 만들어진 DB 가 "정상 v1" 로 남지 않는다.

## 검증

```sql
PRAGMA user_version;        -- 1
PRAGMA journal_mode;        -- wal
PRAGMA foreign_keys;        -- 1  ← 0 이면 트랜잭션 안에서 켠 것이다
PRAGMA foreign_key_check;   -- 빈 결과
PRAGMA integrity_check;     -- ok
SELECT count(*) FROM subjects;   -- 13
```

## 델타 쓸 때 주의

SQLite 는 `CHECK` 제약을 수정할 수 없다. 바꾸려면 **테이블 재생성**이다.

```sql
PRAGMA foreign_keys = OFF;
BEGIN;
  CREATE TABLE t_new (...);
  INSERT INTO t_new SELECT ... FROM t;
  DROP TABLE t;
  ALTER TABLE t_new RENAME TO t;
  -- 인덱스 재생성
COMMIT;
PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;
```

비싸다. 그래서 **바뀔 게 확실한 값은 애초에 CHECK 으로 박지 않는다.**

> **함정: RENAME 뒤에는 테이블 이름에 따옴표가 붙는다.**
> `ALTER TABLE t_new RENAME TO t` 를 하면 `sqlite_master` 에 저장되는 DDL 이
> `CREATE TABLE "t" (...)` 가 된다. `schema.sql` 로 새로 만든 DB 와 텍스트
> 비교를 하면 **이 따옴표 하나 때문에 불일치로 나온다.** 구조는 같다.
> 003 적용 때 실제로 겪었다 — 비교 스크립트가 이걸 걸러내게 하거나,
> 차이가 따옴표뿐인지 눈으로 확인하면 된다.
`final_score` 의 계산식(지금은 `= business_relevance`)이나 `kind × body_mode`
조합 검증이 DDL 이 아니라 collector 에 있는 이유가 이것이다.
