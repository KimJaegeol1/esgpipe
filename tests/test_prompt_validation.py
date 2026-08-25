"""prompt_validation.check() 회귀 테스트.

화면에서 한 번 돌린 검증은 다음에 코드를 고칠 때 아무것도 지켜주지 않는다.
여기 남겨야 회귀를 잡는다.

    cd collector && .venv/bin/python -m pytest ../tests -q
    (pytest 가 없으면)  .venv/bin/python ../tests/test_prompt_validation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

from prompt_validation import SubjectMismatch, check   # noqa: E402

DB = {1: "A", 2: "B", 3: "C"}


def _err(system_prompt, db=DB):
    """어긋나면 메시지, 통과하면 None"""
    try:
        check(system_prompt, db)
        return None
    except SubjectMismatch as e:
        return str(e)


def test_정상():
    assert _err("### 1. A\n### 2. B\n### 3. C") is None


def test_중복_id():
    # dict 로 바로 만들면 중복이 조용히 덮어써진다 — 그래서 따로 센다
    msg = _err("### 1. A\n### 1. A\n### 2. B\n### 3. C")
    assert msg and "중복" in msg and "[1]" in msg


def test_누락_id():
    msg = _err("### 1. A\n### 2. B")
    assert msg and "프롬프트에 없는 id" in msg and "[3]" in msg


def test_추가_id():
    msg = _err("### 1. A\n### 2. B\n### 3. C\n### 9. Z")
    assert msg and "DB 에 없는 id" in msg and "[9]" in msg


def test_이름_불일치():
    msg = _err("### 1. A\n### 2. X\n### 3. C")
    assert msg and "이름 불일치" in msg and "id=2" in msg


def test_여러_문제를_한_번에():
    # 하나 고치고 다시 돌려 다음 걸 찾는 건 느리다
    msg = _err("### 1. A\n### 2. X")
    assert msg and "없는 id" in msg and "이름 불일치" in msg


def test_공백과_들여쓰기를_견딘다():
    assert _err("###   1.   A  \n### 2. B\n### 3. C") is None


def test_subject_가_아닌_제목은_잡지_않는다():
    # "### 숫자." 형식은 subject 정의에만 쓴다는 문법 규칙이 전제다.
    # 이 테스트는 규칙을 지켰을 때 다른 제목이 방해하지 않음을 확인한다
    sp = "## 분류 원칙\n### 1. A\n### 2. B\n### 3. C\n## 경계표\n#### 1 vs 4"
    assert _err(sp) is None


def test_실제_프롬프트가_실제_DB_와_맞는다():
    """운영 파일·DB 를 그대로 본다. 둘 중 하나만 고치면 여기서 걸린다."""
    try:
        from db import connect
        from prompts import load
    except Exception as e:                       # 서버 밖에서 돌릴 때
        print(f"  건너뜀 (서버 환경 아님: {e})")
        return
    p = load("classify")                          # load 안에서 이미 검증한다
    with connect() as c:
        n = c.execute("SELECT count(*) FROM subjects").fetchone()[0]
    assert p["version"] and n == 13


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError:
            bad += 1
            print(f"  FAIL {name}")
        except Exception as e:
            bad += 1
            print(f"  ERR  {name}: {e}")
    print(f"\n{len(fns) - bad}/{len(fns)} 통과")
    sys.exit(1 if bad else 0)
