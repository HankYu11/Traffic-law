"""檢索 → 組提示 → 生成（串流）→ 引用驗證。"""
from __future__ import annotations

import json
import re
import time
from datetime import date

from . import config, llm, sop
from .index import INDEX, Hit, expand_query, status
from .numbers import claims, values

SYSTEM = """你是臺北市政府警察局的「法規與作業程序助理」，服務對象是執勤員警。

規則（務必遵守）：
1. 只能根據 <資料> 內的條文與作業程序回答。資料沒有寫的法條、金額、期間、程序，一律不得自行補充。
2. 每一句陳述事實的句子後面都要標註出處編號，例如 [S2]；同一句有多個出處寫成 [S1][S3]。
3. 金額、期間、濃度等數字必須與條文原文一致，不得換算或推估。
4. 若資料不足以回答，直接回答「查無足夠依據，建議洽詢法制單位或業務主管。」不要猜測。
5. 若資料標示「尚未施行」或「已失效」，回答時必須明確提醒該規定的施行狀態。
6. 若作業程序（SOP）內容與現行條文不一致，以現行條文為準，並提醒「SOP 內容待更新」。
7. 涉及處罰時，結論要完整列出條文規定的所有處分（罰鍰、吊扣／吊銷駕照、移置保管、吊扣牌照、移送法辦等），不可只寫罰鍰。
8. 使用繁體中文，語氣精簡、條列，讓外勤員警一眼看懂。

輸出格式（沒有內容的段落可省略）：
### 結論
（一到三句話直接回答問題）
### 法律依據
- （條文名稱與條號，說明重點）[S#]
### 處理步驟
1. （依作業程序列出步驟）[S#]
### 應備文件
- （表單名稱）[S#]
### 注意事項
- （易錯點、權利告知、施行狀態提醒）[S#]
"""


def format_sources(hits: list[Hit], as_of: date) -> str:
    out = []
    for i, h in enumerate(hits, start=1):
        c = h.chunk
        st = status(c, as_of)
        flag = {"in_force": "現行有效（基準日適用此版本）",
                "pending": f"尚未施行（新規定，{c['effective_from']} 起才施行，基準日不適用）",
                "superseded": f"已失效（{c['effective_to']} 止）"}[st]
        head = f"[S{i}] {c['cite']}｜{c['law_level']}｜版本：{c['version_label']}｜狀態：{flag}"
        if c.get("note"):
            head += f"｜附註：{c['note']}"
        if c["source_type"] == "sop":
            issues = sop.issues_for_step(c["sop_id"], c["step_id"])
            if issues:
                head += "｜⚠ SOP 與現行法規不一致：" + "；".join(i["explanation"] for i in issues)
        out.append(head + "\n" + c["text"])
    return "\n\n".join(out)


def pending_hits(q: str, as_of: date, domain: str | None) -> list[dict]:
    """找出與問題相關、但在基準日「尚未施行」的條項，主動提醒。"""
    hits = INDEX.search(q, as_of=date(2100, 1, 1), domain=domain, k=8, include_sop=False,
                        qvec=INDEX.embed_query(expand_query(q)[0]))
    return [h.chunk for h in hits if status(h.chunk, as_of) == "pending"]


def should_refuse(hits: list[Hit]) -> bool:
    if not hits:
        return True
    if any(h.exact for h in hits):
        return False
    return max(h.dense for h in hits if h.score > 0) < config.MIN_DENSE_SCORE


CITE_RE = re.compile(r"\[S(\d+)\]")


def expand_with_sop_links(hits: list[Hit], as_of: date, limit: int = 12) -> list[Hit]:
    """SOP 步驟被檢索到時，把它引用的法源條文（基準日有效版本）一併帶入出處。"""
    have = {h.chunk["id"] for h in hits}
    out = list(hits)
    for h in hits:
        c = h.chunk
        if c["source_type"] != "sop":
            continue
        st = next((x for x in sop.all_sops()[c["sop_id"]]["steps"] if x["id"] == c["step_id"]), None)
        for ref in (st or {}).get("legal_basis", []) or []:
            for lc in INDEX.current(ref["pcode"], ref["article"], as_of, ref.get("paragraph")):
                if lc["id"] not in have and len(out) < limit:
                    have.add(lc["id"])
                    out.append(Hit(lc, 0.0, 0.0, 0.0, False))
    return out


def verify(answer: str, hits: list[Hit], question: str = "") -> dict:
    """引用驗證：出處編號是否存在、每句是否附出處、數字是否出現在被引用的條文中。"""
    n = len(hits)
    cited = [int(x) for x in CITE_RE.findall(answer)]
    invalid = sorted({x for x in cited if x < 1 or x > n})
    sentences = []
    for line in answer.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        sentences.append(s)
    uncited = [s for s in sentences if not CITE_RE.search(s) and len(s) > 12 and "查無足夠依據" not in s]

    number_issues = []
    q_values = values(question)  # 使用者自己提供的數字（例如酒測值）不算模型捏造
    for s in sentences:
        ids = [int(x) for x in CITE_RE.findall(s) if 1 <= int(x) <= n]
        if not ids:
            continue
        pool = set(q_values)
        for i in ids:
            c = hits[i - 1].chunk
            pool |= values(c["text"])
            if c["source_type"] == "sop":  # SOP 步驟：連同其法源條文與複核結果一起比對
                for iss in sop.issues_for_step(c["sop_id"], c["step_id"]):
                    pool |= values(iss.get("law_text", "") + iss.get("explanation", ""))
                for h in hits:
                    if h.chunk["source_type"] == "law":
                        pool |= values(h.chunk["text"])
        for raw, v in claims(CITE_RE.sub("", s)):
            if v not in pool:
                number_issues.append({"sentence": s, "value": raw})
    used = sorted(set(x for x in cited if 1 <= x <= n))
    ok = not invalid and not number_issues
    return {
        "ok": ok,
        "cited_sources": used,
        "invalid_citations": invalid,
        "uncited_sentences": uncited,
        "number_issues": number_issues,
        "checked_sentences": len(sentences),
    }


def hit_json(h: Hit, i: int, as_of: date) -> dict:
    c = h.chunk
    d = {k: c.get(k) for k in ("id", "source_type", "pcode", "law_name", "law_level", "article_no", "article_label",
                                "paragraph", "cite", "text", "article_text", "version_label", "effective_from",
                                "effective_to", "note", "url", "chapter", "sop_id", "step_id", "source_page")}
    d.update(n=i, status=status(c, as_of), score=round(h.score, 4), dense=round(h.dense, 3), exact=h.exact)
    if c["source_type"] == "sop":
        d["sop_issues"] = sop.issues_for_step(c["sop_id"], c["step_id"])
    return d


async def ask_stream(q: str, as_of: date, domain: str | None, history: list[dict] | None = None):
    """SSE 事件產生器。"""
    t0 = time.perf_counter()
    search_q = q
    if history:
        prev = [m["content"] for m in history if m.get("role") == "user"][-2:]
        search_q = " ".join(prev + [q])
    hits = INDEX.search(search_q, as_of=as_of, domain=domain)
    hits = expand_with_sop_links(hits, as_of)
    pend = pending_hits(search_q, as_of, domain)
    have = {h.chunk["id"] for h in hits}
    hits += [Hit(c, 0.0, 0.0, 0.0, False) for c in pend[:3] if c["id"] not in have]
    t_retr = time.perf_counter() - t0
    sources = [hit_json(h, i, as_of) for i, h in enumerate(hits, start=1)]
    sop_ids = sorted({c["sop_id"] for c in (h.chunk for h in hits) if c["source_type"] == "sop"})
    yield {"type": "sources", "sources": sources, "retrieval_ms": round(t_retr * 1000),
           "expansions": expand_query(search_q)[1],
           "pending": [{"cite": c["cite"], "effective_from": c["effective_from"], "note": c.get("note", ""),
                        "text": c["text"]} for c in pend],
           "sops": sop_ids}

    if should_refuse(hits):
        msg = "查無足夠依據。此問題可能不在目前收錄的法規範圍（交通、酒駕）內，建議洽詢法制單位或業務主管。"
        yield {"type": "token", "text": msg}
        yield {"type": "done", "answer": msg, "refused": True,
               "verification": {"ok": True, "cited_sources": [], "invalid_citations": [], "uncited_sentences": [],
                                "number_issues": [], "checked_sentences": 0},
               "timing": {"retrieval_ms": round(t_retr * 1000), "total_ms": round((time.perf_counter() - t0) * 1000)}}
        return

    messages = [{"role": "system", "content": SYSTEM}]
    for m in (history or [])[-4:]:
        messages.append({"role": m["role"], "content": m["content"]})
    pend_txt = ""
    if pend:
        pend_txt = ("\n\n提醒：資料中標示「尚未施行」的條項是已公布但在基準日還沒生效的新規定，"
                    "回答時以「現行有效」的版本為準，並在注意事項說明新規定的施行日期。")
    messages.append({"role": "user", "content":
                     f"查詢基準日：{as_of.isoformat()}\n\n<資料>\n{format_sources(hits, as_of)}\n</資料>{pend_txt}\n\n問題：{q}"})

    answer = ""
    t_first = None
    async for kind, text in llm.stream(messages):
        if kind == "thinking":
            yield {"type": "thinking"}
            continue
        if t_first is None:
            t_first = time.perf_counter() - t0
        answer += text
        yield {"type": "token", "text": text}
    v = verify(answer, hits, q)
    yield {"type": "done", "answer": answer, "refused": False, "verification": v,
           "timing": {"retrieval_ms": round(t_retr * 1000),
                      "first_token_ms": round((t_first or 0) * 1000),
                      "total_ms": round((time.perf_counter() - t0) * 1000)}}


async def ask(q: str, as_of: date, domain: str | None = None) -> dict:
    """非串流版本（評估腳本使用）。"""
    result = {}
    async for ev in ask_stream(q, as_of, domain):
        if ev["type"] == "sources":
            result["sources"] = ev["sources"]
            result["pending"] = ev["pending"]
        elif ev["type"] == "done":
            result.update(ev)
    return result


async def ask_baseline(q: str) -> str:
    """對照組：不給任何資料，直接問模型。"""
    return await llm.complete([
        {"role": "system", "content": "你是臺灣的警察法規助理，請用繁體中文精簡回答，並列出法律依據條號。"},
        {"role": "user", "content": q},
    ], max_tokens=4096)


def dumps(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
