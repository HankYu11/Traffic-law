"""查詢紀錄與回饋（F7 數據紀錄）。SQLite，地端部署可換 PostgreSQL。"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, question TEXT, domain TEXT, as_of TEXT, mode TEXT,
  answer TEXT, refused INTEGER, verification TEXT, sources TEXT,
  retrieval_ms INTEGER, first_token_ms INTEGER, total_ms INTEGER
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, query_id INTEGER, rating INTEGER, comment TEXT, needs_human INTEGER
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    from pathlib import Path

    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


def log_query(question, domain, as_of, mode, done: dict, sources: list) -> int:
    t = done.get("timing", {})
    with conn() as c:
        cur = c.execute(
            "INSERT INTO queries (ts, question, domain, as_of, mode, answer, refused, verification, sources,"
            " retrieval_ms, first_token_ms, total_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), question, domain, as_of, mode, done.get("answer", ""), int(done.get("refused", False)),
             json.dumps(done.get("verification", {}), ensure_ascii=False),
             json.dumps([s["cite"] for s in sources], ensure_ascii=False),
             t.get("retrieval_ms"), t.get("first_token_ms"), t.get("total_ms")),
        )
        return cur.lastrowid


def add_feedback(query_id: int, rating: int, comment: str, needs_human: bool):
    with conn() as c:
        c.execute("INSERT INTO feedback (ts, query_id, rating, comment, needs_human) VALUES (?,?,?,?,?)",
                  (time.time(), query_id, rating, comment, int(needs_human)))


def history(limit=50, q: str | None = None) -> list[dict]:
    with conn() as c:
        if q:
            rows = c.execute("SELECT * FROM queries WHERE question LIKE ? ORDER BY id DESC LIMIT ?",
                             (f"%{q}%", limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM queries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_query(qid: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM queries WHERE id=?", (qid,)).fetchone()
    return dict(r) if r else None


def stats() -> dict:
    with conn() as c:
        q = c.execute("SELECT COUNT(*) n, SUM(refused) refused, AVG(retrieval_ms) retr, AVG(total_ms) total,"
                      " AVG(first_token_ms) ttft FROM queries WHERE mode='rag'").fetchone()
        f = c.execute("SELECT COUNT(*) n, AVG(rating) avg, SUM(rating>0) up, SUM(rating<0) down,"
                      " SUM(needs_human) human FROM feedback").fetchone()
        by_domain = c.execute("SELECT COALESCE(domain,'all') d, COUNT(*) n FROM queries WHERE mode='rag'"
                              " GROUP BY d").fetchall()
        per_day = c.execute("SELECT date(ts,'unixepoch','localtime') d, COUNT(*) n FROM queries"
                            " WHERE mode='rag' GROUP BY d ORDER BY d").fetchall()
        rows = c.execute("SELECT verification FROM queries WHERE mode='rag' AND refused=0").fetchall()
    verified = sum(1 for r in rows if json.loads(r["verification"] or "{}").get("ok"))
    return {
        "queries": q["n"] or 0, "refused": q["refused"] or 0,
        "avg_retrieval_ms": round(q["retr"] or 0), "avg_total_ms": round(q["total"] or 0),
        "avg_first_token_ms": round(q["ttft"] or 0),
        "verified_ok": verified, "answered": len(rows),
        "feedback": {"n": f["n"] or 0, "up": f["up"] or 0, "down": f["down"] or 0, "needs_human": f["human"] or 0},
        "by_domain": [dict(r) for r in by_domain], "per_day": [dict(r) for r in per_day],
    }
