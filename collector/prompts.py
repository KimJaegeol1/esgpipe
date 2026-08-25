"""프롬프트 서빙 — 정본은 레포의 md, 워크플로에는 복사본을 두지 않는다.

워크플로 안의 프롬프트는 schema.sql 과 델타 같은 "둘 다 필요한 산출물"이
아니라 **없어도 되는 파생본**이다. 자주 고치는 단계일수록 복사본의 동기화
실패 가능성이 크다.

@lru_cache 를 걸지 않는다. 작은 md 를 실행당 한 번 읽는 비용은 무시할
수준이고, 캐시를 걸면 프롬프트를 고칠 때마다 재시작해야 한다.
"""
import hashlib
from pathlib import Path

from config import get_settings, get_tuning

# 프롬프트 이름 → 디렉터리. 버전은 tuning.yaml 이 정한다
_DIRS = {
    "classify": "3_classify",
    "analyze":  "4_analyze",
    "cluster":  "5_cluster",
    "compose":  "6_compose",
}
_PARTS = ("system.md", "user.md")   # 해시 계산 순서. 바꾸면 해시가 달라진다


def _root() -> Path:
    # db/ 와 형제인 prompts/
    return get_settings().db_path.parent.parent / "prompts"


def load(name: str) -> dict:
    if name not in _DIRS:
        raise KeyError(f"알 수 없는 프롬프트: {name}")
    version = get_tuning()["prompt_versions"][name]
    d = _root() / _DIRS[name] / f"v{version}"

    h = hashlib.sha256()
    texts = {}
    for part in _PARTS:
        f = d / part
        if not f.exists():
            raise FileNotFoundError(f"{f} 가 없다")
        b = f.read_bytes()
        h.update(b)                       # UTF-8 바이트를 고정된 순서로
        texts[part] = b.decode("utf-8")

    return {
        "name": name,
        "version": version,
        # 식별 신호이지 불변성 강제가 아니다. 같은 버전인데 해시가 달라졌다면
        # "쓰인 버전은 고치지 않는다"는 규칙이 깨진 것이고, 알아차릴 수단일
        # 뿐 막아주지는 않는다
        "sha256": h.hexdigest(),
        "system_prompt": texts["system.md"],
        "user_template": texts["user.md"],
    }
