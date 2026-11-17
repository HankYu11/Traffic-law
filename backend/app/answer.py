"""檢索 → 組提示 → 生成（串流）→ 引用驗證。"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from . import config, llm, sop
from .index import INDEX, Hit, expand_query, status
from .numbers import claims, values

SYSTEM = """你是臺北市政府警察局的法規與作業程序助理，服務執勤員警。規則：
1. 只根據 <資料> 回答；資料沒寫的法條、金額、期間、程序不得補充。資料不足就回答「查無足夠依據，建議洽詢法制單位或業務主管。」
2. 每句事實後標出處，如 [S2]；多個出處寫成 [S1][S3]。數字必須與條文一致。
3. 只回答問題問的情形（酒精≠毒品、汽車≠機車≠慢車、初犯≠再犯），不要列出其他情形的罰則。條文中「〔〕」是系統展開的參照說明，用來判斷適用情形，不是原文。
4. 處罰題要列齊該情形的所有處分（罰鍰、吊扣／吊銷駕照、移置保管、扣繳／吊扣牌照、移送法辦）；程序題要列齊要件與例外。
5. 標示「尚未施行」的是新規定，以「現行有效」版本回答，並在注意事項說明施行日。
6. SOP 與現行條文不一致時以條文為準，並提醒「SOP 內容待更新」。
7. 繁體中文，精簡條列。

格式（沒有內容的段落省略）：
### 結論
（第一句直接回答：適用哪一條、罰多少／怎麼做）
### 法律依據
- 條文與重點 [S#]
### 處理步驟
1. 步驟 [S#]
### 應備文件
- 表單 [S#]
### 注意事項
- 易錯點、施行狀態 [S#]
"""


GLOSSARY_PATH = Path(__file__).resolve().parents[2] / "data" / "glossary.yaml"


@lru_cache(maxsize=1)
def glossary() -> list[dict]:
    if not GLOSSARY_PATH.exists():
        return []
    return yaml.safe_load(GLOSSARY_PATH.read_text(encoding="utf-8")) or []


def _matches(entry: dict, c: dict) -> bool:
    ref = entry["cite"]
    if c["source_type"] != "law" or c["pcode"] != ref["pcode"] or c["article_no"] != str(ref["article"]):
        return False
    return not ref.get("paragraph") or (c["paragraph"] or 1) == ref["paragraph"]


def glossary_note(c: dict) -> str:
    return "；".join(e["text"] for e in glossary() if _matches(e, c))


def add_definitions(hits: list[Hit], q: str, as_of: date, limit: int = 2) -> list[Hit]:
    """問題提到「機車」「腳踏車」等用詞時，帶入定義條文，避免模型以為資料沒寫到。"""
    have = {h.chunk["id"] for h in hits}
    out = list(hits)
    added = 0
    for e in glossary():
        if added >= limit or not any(t in q for t in e.get("triggers") or []):
            continue
        ref = e["cite"]
        for c in INDEX.current(ref["pcode"], str(ref["article"]), as_of, ref.get("paragraph")):
            if c["id"] not in have:
                have.add(c["id"])
                out.append(Hit(c, 0.0, 0.0, 0.0, False))
                added += 1
    return out


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
        body = c.get("annotated", c["text"])
        if note := glossary_note(c):
            body += f"\n〔說明：{note}〕"
        out.append(head + "\n" + body)
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
MULTI_CITE_RE = re.compile(r"[\[【]\s*(S\d+(?:\s*[,，、]\s*S?\d+)+)\s*[\]】]")


def normalize_cites(text: str) -> str:
    """把「[S1, S8]」「[S1、3]」統一成「[S1][S8]」。"""
    def repl(m):
        ids = re.findall(r"\d+", m.group(1))
        return "".join(f"[S{i}]" for i in ids)
    return MULTI_CITE_RE.sub(repl, text)


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


def add_main_paragraphs(hits: list[Hit], as_of: date, limit: int = 3) -> list[Hit]:
    """主項補全：取到某條的後段項次時，一併帶入該條第 1 項（多數後段項次都是「依第一項…」）。"""
    have = {h.chunk["id"] for h in hits}
    added = 0
    out = list(hits)
    for h in hits:
        c = h.chunk
        if c["source_type"] != "law" or not c["paragraph"] or c["paragraph"] == 1 or added >= limit:
            continue
        for mc in INDEX.current(c["pcode"], c["article_no"], as_of, 1):
            if mc["id"] not in have:
                have.add(mc["id"])
                # 插在該項前面，讓模型先讀到主規定
                out.insert(out.index(h), Hit(mc, h.score, h.dense, 0.0, False))
                added += 1
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
            pool |= values(c["text"] + glossary_note(c))  # 法規室說明的數字（如 0.17）也算有據
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
    hits = add_main_paragraphs(hits, as_of)
    hits = expand_with_sop_links(hits, as_of)
    hits = add_definitions(hits, q, as_of)
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
    for _attempt in range(2):  # 服務忙碌時偶爾回空答案：重試一次
        async for kind, text in llm.stream(messages, max_tokens=8192, temperature=0.1):
            if kind == "thinking":
                yield {"type": "thinking"}
                continue
            if t_first is None:
                t_first = time.perf_counter() - t0
            answer += text
            yield {"type": "token", "text": text}
        if answer.strip():
            break
    answer = normalize_cites(answer)
    if not answer.strip():
        raise RuntimeError("模型沒有產生答案（可能是推理過長或服務忙碌），請再試一次。")
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
