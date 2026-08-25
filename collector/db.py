"""DB 연결 — collector 가 esg.db 의 유일한 소유자다.

연결을 여는 곳은 여기 하나뿐이다. 흩어지면 PRAGMA 를 빼먹게 되고,
foreign_keys 는 꺼져도 에러가 안 나서 그대로 돌아간다.
"""
import sqlite3
from contextlib import contextmanager

from config import get_settings


@contextmanager
def connect():
    """읽기·쓰기 공용. 나갈 때 반드시 닫는다."""
    conn = sqlite3.connect(get_settings().db_path, timeout=10.0)
    try:
        # 연결마다 켜야 한다. 파일에 안 남는다
        conn.execute("PRAGMA foreign_keys = ON")
        # 컬럼명으로 접근한다 — row[3] 은 스키마가 바뀌면 조용히 틀린다
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def health() -> dict:
    """DB 가 살아 있고 우리가 아는 모양인지."""
    with connect() as c:
        q = lambda sql: c.execute(sql).fetchone()[0]
        return {
            "user_version": q("PRAGMA user_version"),
            "journal_mode": q("PRAGMA journal_mode"),
            "foreign_keys": q("PRAGMA foreign_keys"),
            "subjects":     q("SELECT count(*) FROM subjects"),
            "sources":      q("SELECT count(*) FROM sources"),
            "articles":     q("SELECT count(*) FROM articles"),
        }
