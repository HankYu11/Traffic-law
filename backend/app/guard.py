"""公開網址的保護：存取碼、每日 LLM 呼叫上限、答案快取。

- ACCESS_CODE：設定後，所有會呼叫 LLM 的 API 都要帶 X-Access-Code 標頭。
- DAILY_LLM_LIMIT：每天最多幾次 LLM 呼叫（快取命中不算），預設 300；0 = 不限制。
- 答案快取：同一題（同業務分頁、同基準日、沒有對話脈絡）直接重播上次的答案，不再花 API。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import date

from fastapi import Header, HTTPException

from . import db

ACCESS_CODE = os.getenv("ACCESS_CODE", "")
DAILY_LLM_LIMIT = int(os.getenv("DAILY_LLM_LIMIT", "300"))
MAX_QUESTION_CHARS = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS answer_cache (k TEXT PRIMARY KEY, ts REAL, events TEXT);
CREATE TABLE IF NOT EXISTS llm_usage (day TEXT PRIMARY KEY, n INTEGER);
"""


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


def check_code(x_access_code: str | None = Header(default=None)):
    if ACCESS_CODE and not secrets.compare_digest(x_access_code or "", ACCESS_CODE):
        raise HTTPException(401, "需要存取碼")


def check_question(q: str):
    if not q.strip():
        raise HTTPException(400, "問題不可空白")
    if len(q) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"問題請在 {MAX_QUESTION_CHARS} 字以內")


def take_quota(n: int = 1):
    """扣一次 LLM 額度；超過當日上限就拒絕。"""
    if DAILY_LLM_LIMIT <= 0:
        return
    day = date.today().isoformat()
    with db.conn() as c:
        row = c.execute("SELECT n FROM llm_usage WHERE day=?", (day,)).fetchone()
        used = row["n"] if row else 0
        if used + n > DAILY_LLM_LIMIT:
            raise HTTPException(429, f"今日示範額度（{DAILY_LLM_LIMIT} 次）已用完，請明天再試，或改用「快速查條文」（不經 AI）。")
        c.execute("INSERT INTO llm_usage (day, n) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET n = n + ?",
                  (day, n, n))


def usage() -> dict:
    day = date.today().isoformat()
    with db.conn() as c:
        row = c.execute("SELECT n FROM llm_usage WHERE day=?", (day,)).fetchone()
        cached = c.execute("SELECT COUNT(*) n FROM answer_cache").fetchone()["n"]
    return {"today": row["n"] if row else 0, "limit": DAILY_LLM_LIMIT, "cached_answers": cached}


def cache_key(q: str, domain: str | None, as_of: str, model: str) -> str:
    norm = re.sub(r"[\s？?。！!，,]", "", q)
    return hashlib.sha1(f"{norm}|{domain}|{as_of}|{model}".encode()).hexdigest()


def cache_get(k: str) -> list[dict] | None:
    with db.conn() as c:
        row = c.execute("SELECT events FROM answer_cache WHERE k=?", (k,)).fetchone()
    return json.loads(row["events"]) if row else None


def cache_put(k: str, events: list[dict]):
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO answer_cache (k, ts, events) VALUES (?,?,?)",
                  (k, time.time(), json.dumps(events, ensure_ascii=False)))
