"""OpenAI 相容 Chat Completions 用戶端（Gemini API / vLLM / Ollama 皆可）。

Gemma 4 經 Gemini API 時會先輸出 <thought>…</thought> 推理內容；這裡把它濾掉，只回傳答案。
在 vLLM 上可以直接關閉推理模式，首字延遲會大幅降低。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator

import logging

import httpx

from . import config

log = logging.getLogger("llm")
THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.S)


def _payload(messages, max_tokens, stream, temperature, model=None):
    return {
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }


def _headers():
    return {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}


RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_RETRIES = 6


def _backoff(attempt: int, r: httpx.Response | None = None) -> float:
    ra = r.headers.get("retry-after") if r is not None else None
    if ra and ra.replace(".", "").isdigit():
        return min(float(ra), 60)
    return min(2 ** attempt * 2, 60)  # 2, 4, 8, 16, 32, 60 秒


async def complete(messages, max_tokens=4096, temperature=0.1, model: str | None = None) -> str:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as c:
        for attempt in range(MAX_RETRIES + 1):
            r = await c.post(f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(),
                             json=_payload(messages, max_tokens, False, temperature, model))
            if r.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                log.warning("LLM %s，第 %d 次重試（等待 %.0f 秒）", r.status_code, attempt + 1, _backoff(attempt, r))
                await asyncio.sleep(_backoff(attempt, r))
                continue
            r.raise_for_status()
            break
        text = r.json()["choices"][0]["message"].get("content") or ""
    return THOUGHT_RE.sub("", text).strip()


async def _raw_stream(c: httpx.AsyncClient, messages, max_tokens, temperature) -> AsyncIterator[str]:
    """送出串流請求並逐段產生文字；遇到 429／5xx 在收到任何內容前自動重試。"""
    for attempt in range(MAX_RETRIES + 1):
        async with c.stream("POST", f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(),
                            json=_payload(messages, max_tokens, True, temperature)) as r:
            if r.status_code in RETRY_STATUS and attempt < MAX_RETRIES:
                await r.aread()
                log.warning("LLM %s，第 %d 次重試（等待 %.0f 秒）", r.status_code, attempt + 1, _backoff(attempt, r))
                yield ""  # 讓上層知道還在等
                await asyncio.sleep(_backoff(attempt, r))
                continue
            if r.status_code >= 400:
                body = await r.aread()
                raise RuntimeError(f"LLM error {r.status_code}: {body[:300]!r}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    yield delta
            return


THINK_TIMEOUT = float(__import__("os").getenv("THINK_TIMEOUT", "15"))
THINK_RESTARTS = int(__import__("os").getenv("THINK_RESTARTS", "3"))


async def stream(messages, max_tokens=4096, temperature=0.1) -> AsyncIterator[tuple[str, str]]:
    """產生 (kind, text)：kind = 'thinking'（推理中，僅回報進度）或 'token'（答案文字）。

    Gemini API 上的 Gemma 4 無法關閉推理，偶爾會推理很久（60 秒以上）。
    推理超過 THINK_TIMEOUT 秒、尚未輸出答案時，取消並重新請求（最多 THINK_RESTARTS 次；重抽通常不會再長考），
    最後一次不設限。
    地端 vLLM 可直接關閉推理，不會有這個問題。
    """
    loop = asyncio.get_running_loop()
    for attempt in range(THINK_RESTARTS + 1):
        buf = ""
        in_thought: bool | None = None  # None = 尚未判斷
        emitted = False
        t0 = loop.time()
        restart = False
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as c:
            async for delta in _raw_stream(c, messages, max_tokens, temperature):
                if not emitted and attempt < THINK_RESTARTS and loop.time() - t0 > THINK_TIMEOUT:
                    log.warning("推理超過 %.0f 秒，重新請求", THINK_TIMEOUT)
                    restart = True
                    break
                if not delta:
                    yield "thinking", ""
                    continue
                buf += delta
                if in_thought is None:
                    st = buf.lstrip()
                    if len(st) < 9 and "<thought>".startswith(st):
                        continue  # 還不能判斷
                    in_thought = st.startswith("<thought>")
                    if not in_thought:
                        emitted = True
                        yield "token", buf
                        buf = ""
                    continue
                if in_thought:
                    if "</thought>" in buf:
                        rest = buf.split("</thought>", 1)[1]
                        in_thought = False
                        buf = ""
                        if rest:
                            emitted = True
                            yield "token", rest
                    else:
                        yield "thinking", ""
                else:
                    emitted = True
                    yield "token", buf
                    buf = ""
        if restart:
            continue
        if buf and not in_thought:
            yield "token", buf
        return
