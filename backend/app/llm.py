"""OpenAI 相容 Chat Completions 用戶端（Gemini API / vLLM / Ollama 皆可）。

Gemma 4 經 Gemini API 時會先輸出 <thought>…</thought> 推理內容；這裡把它濾掉，只回傳答案。
在 vLLM 上可以直接關閉推理模式，首字延遲會大幅降低。
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator

import httpx

from . import config

THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.S)


def _payload(messages, max_tokens, stream, temperature):
    return {
        "model": config.LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }


def _headers():
    return {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}


async def complete(messages, max_tokens=4096, temperature=0.1) -> str:
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as c:
        for attempt in range(3):
            r = await c.post(f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(),
                             json=_payload(messages, max_tokens, False, temperature))
            if r.status_code in (429, 500, 502, 503) and attempt < 2:
                import asyncio
                await asyncio.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        text = r.json()["choices"][0]["message"].get("content") or ""
    return THOUGHT_RE.sub("", text).strip()


async def stream(messages, max_tokens=4096, temperature=0.1) -> AsyncIterator[tuple[str, str]]:
    """產生 (kind, text)：kind = 'thinking'（推理中，僅回報進度）或 'token'（答案文字）。"""
    buf = ""
    in_thought: bool | None = None  # None = 尚未判斷
    async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT) as c:
        async with c.stream("POST", f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(),
                            json=_payload(messages, max_tokens, True, temperature)) as r:
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
                if not delta:
                    continue
                buf += delta
                if in_thought is None:
                    s = buf.lstrip()
                    if len(s) < 9 and "<thought>".startswith(s):
                        continue  # 還不能判斷
                    in_thought = s.startswith("<thought>")
                    if not in_thought:
                        yield "token", buf
                        buf = ""
                    continue
                if in_thought:
                    if "</thought>" in buf:
                        rest = buf.split("</thought>", 1)[1]
                        in_thought = False
                        buf = ""
                        if rest:
                            yield "token", rest
                    else:
                        yield "thinking", ""
                else:
                    yield "token", buf
                    buf = ""
    if buf and not in_thought:
        yield "token", buf
