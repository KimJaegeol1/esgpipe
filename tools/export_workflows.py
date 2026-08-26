#!/usr/bin/env python3
"""n8n DB 에서 워크플로를 읽어 n8n/*.json 으로 뽑는다.

n8n 화면에서 Download 하고 FileZilla 로 올리는 왕복을 없앤다.
**읽기 전용이다** — n8n DB 를 건드리지 않는다.

    python3 tools/export_workflows.py           뽑는다
    python3 tools/export_workflows.py --check   레포와 다른지만 본다 (쓰지 않음)

n8n 데이터 폴더를 esgpipe 안으로 옮기지 않는 이유:
database.sqlite 가 레포에 들어오면 16M 바이너리가 계속 커지고,
**암호화된 크레덴셜이 그 안에 있다.** esg.db 를 커밋 안 하기로 한 것과
같은 이유 — 런타임 상태와 레포를 섞지 않는다.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

N8N_DB = Path("/mnt/data/n8n/.n8n/database.sqlite")
OUT = Path(__file__).resolve().parent.parent / "n8n"
PREFIX = "esgpipe"

# n8n 이 붙이는 런타임 값. 레포에 넣으면 실행마다 diff 가 생겨
# "무엇이 실제로 바뀌었나"를 못 본다
DROP = ("id", "versionId", "meta", "pinData", "staticData",
        "triggerCount", "createdAt", "updatedAt", "isArchived",
        "versionCounter", "parentFolderId", "activeVersionId")


def dump(row) -> dict:
    wf = {
        "name": row["name"],
        "nodes": json.loads(row["nodes"]),
        "connections": json.loads(row["connections"]),
        "settings": json.loads(row["settings"] or "{}"),
        "active": bool(row["active"]),
    }
    for k in DROP:
        wf.pop(k, None)
    return wf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="파일을 쓰지 않고 다른 것만 보고한다")
    args = ap.parse_args()

    if not N8N_DB.exists():
        print(f"n8n DB 가 없다: {N8N_DB}", file=sys.stderr)
        return 1

    c = sqlite3.connect(f"file:{N8N_DB}?mode=ro", uri=True)  # 읽기 전용
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT * FROM workflow_entity WHERE name LIKE ? ORDER BY name",
        (PREFIX + "%",)).fetchall()
    if not rows:
        print(f"'{PREFIX}' 로 시작하는 워크플로가 없다", file=sys.stderr)
        return 1

    OUT.mkdir(exist_ok=True)
    changed = []
    for r in rows:
        wf = dump(r)
        text = json.dumps(wf, ensure_ascii=False, indent=2) + "\n"
        f = OUT / f"{r['name']}.json"
        old = f.read_text(encoding="utf-8") if f.exists() else None
        state = "new" if old is None else ("changed" if old != text else "same")
        if state != "same":
            changed.append(r["name"])
            if not args.check:
                f.write_text(text, encoding="utf-8")
        mark = {"new": "+", "changed": "~", "same": " "}[state]
        print(f"  {mark} {r['name']:<22} 노드 {len(wf['nodes']):>2}"
              f"  active={wf['active']}  {len(text):>6}자")

    if args.check:
        print(f"\n다른 것 {len(changed)}개" + (f": {changed}" if changed else ""))
        return 1 if changed else 0
    print(f"\n{len(rows)}개 확인 · {len(changed)}개 갱신 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
