"""프롬프트와 DB 의 subject 목록이 어긋났는지 본다.

**요청 시 검증이 필수다.** 프롬프트를 캐시하지 않으므로 서비스가 뜬 뒤
system.md 가 수정되면 시작 검증만으로는 못 잡는다 — 통과한 상태로 잘못된
프롬프트를 계속 서빙한다.

LLM 호출 전에 막는 게 목적이다. 어긋나면 82콜을 다 쓰고 전부 422 로
거부된다. 그렇다고 시작 검증으로 서비스를 못 뜨게 하면 분류 프롬프트
하나 때문에 /ingest 까지 멈춘다 — 영향 범위가 맞는 곳에서 막는다.
"""
import re
from collections import Counter

# system.md 의 "### 3. 탄소국경조정과 무역 규제" 형태.
# 이 정규식은 System Prompt 전체의 모든 "### 숫자." 제목을 subject 로 본다 —
# system.md 에서 이 형식은 subject 정의에만 쓴다는 문법 규칙이 전제다.
# docs/3_classify.md 에 명시돼 있다.
_HEADING = re.compile(r'^###\s+(\d+)\.\s*(.+?)\s*$', re.M)


class SubjectMismatch(Exception):
    pass


def check(system_prompt: str, db_subjects: dict[int, str]) -> None:
    """어긋나면 SubjectMismatch. 아니면 조용히 반환한다."""
    pairs = [(int(i), n) for i, n in _HEADING.findall(system_prompt)]

    # dict 로 바로 만들면 중복 id 가 조용히 덮어써진다
    counts = Counter(i for i, _ in pairs)
    dup = sorted(i for i, n in counts.items() if n > 1)
    if dup:
        raise SubjectMismatch(f"프롬프트에 중복 subject id: {dup}")

    prompt = dict(pairs)
    problems = []
    missing = sorted(set(db_subjects) - set(prompt))
    extra   = sorted(set(prompt) - set(db_subjects))
    if missing:
        problems.append(f"프롬프트에 없는 id: {missing}")
    if extra:
        problems.append(f"DB 에 없는 id: {extra}")
    for i in sorted(set(db_subjects) & set(prompt)):
        if db_subjects[i] != prompt[i]:
            problems.append(
                f"id={i} 이름 불일치 · DB {db_subjects[i]!r} · 프롬프트 {prompt[i]!r}")
    if problems:
        raise SubjectMismatch(" / ".join(problems))
