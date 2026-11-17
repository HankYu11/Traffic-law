"""SOP 與現行法規一致性檢查（法規異動影響分析）。

對 SOP 每一個步驟，取出它引用的法源「現行條文」，請 LLM 逐項比對金額、期間、門檻、條號，
找出 SOP 內容已經跟不上修法的地方。結果標示為「AI 比對，待法規室確認」。

用法：python ingest/sop_check.py [as_of=YYYY-MM-DD]
輸出：data/processed/sop_check.json
"""
import asyncio
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, llm, sop  # noqa: E402
from app.index import INDEX, status  # noqa: E402

PROMPT = """你是法制作業審查員。請比對「SOP 步驟內容」與「現行法規條文」，找出 SOP 中已經與現行條文不一致的具體敘述
（例如罰鍰金額、吊扣吊銷期間、濃度門檻、引用條號、程序要件）。

只回報「確定不一致」的地方，措辭差異或 SOP 較詳細的實務補充不算。不要臆測條文沒有寫的內容。
同一條文常對不同情形有不同規定（例如酒精 vs. 毒品、汽車 vs. 慢車、初犯 vs. 再犯），務必對應到 SOP 所描述的同一種情形再比較。
sop_text 與 law_text 必須逐字摘自下方原文。
以 JSON 輸出，格式：
{{"issues": [{{"sop_text": "SOP 中有問題的原句", "law_cite": "現行條文出處", "law_text": "現行條文中對應的原句",
  "explanation": "一句話說明哪裡不一致、應如何更新"}}]}}
沒有不一致就輸出 {{"issues": []}}。只輸出 JSON。

<SOP 步驟：{title}>
{sop_text}
</SOP 步驟>

<現行法規條文（基準日 {as_of}）>
{law_text}
</現行法規條文>
"""


def law_refs(st, as_of):
    out = []
    for ref in st.get("legal_basis", []) or []:
        cs = [c for c in INDEX.article_versions(ref["pcode"], ref["article"]) if status(c, as_of) == "in_force"]
        if ref.get("paragraph"):
            cs = [c for c in cs if (c["paragraph"] or 1) == ref["paragraph"]]
        for c in cs:
            out.append(f"【{c['cite']}】{c['text']}")
    return out


async def check_step(s, st, as_of):
    refs = law_refs(st, as_of)
    if not refs:
        return []
    sop_text = "\n".join(st.get("actions", []) + st.get("cautions", []))
    raw = await llm.complete([{"role": "user", "content": PROMPT.format(
        title=st["title"], sop_text=sop_text, law_text="\n\n".join(refs), as_of=as_of.isoformat())}],
        max_tokens=8192, temperature=0)
    issues = parse_json(raw).get("issues", [])
    law_all = norm("\n".join(refs))
    sop_all = norm(sop_text)
    kept = []
    for i in issues:
        # 1) 引文落地檢查：SOP 原句與條文原句都必須真的出現在輸入中（防止模型捏造引文）
        if norm(i.get("sop_text", ""))[:20] not in sop_all or norm(i.get("law_text", ""))[:20] not in law_all:
            print("   [drop: 引文不在原文]", i.get("explanation", "")[:60])
            continue
        # 2) 第二次獨立確認：同一題換個角度問「這真的是矛盾嗎？」
        if not await confirm(i, "\n\n".join(refs)):
            print("   [drop: 複核未通過]", i.get("explanation", "")[:60])
            continue
        kept.append(dict(i, step_id=st["id"], step_title=st["title"], status="待法規室確認"))
    return kept


VERIFY = """請嚴格複核以下「SOP 與現行法規不一致」的判斷是否成立。

判斷成立的條件：SOP 原句所描述的「同一種情形」，在現行條文中有明確不同的規定（金額、期間、門檻、要件不同）。
以下情形判斷「不成立」：
- 條文原句講的是另一種情形（例如毒品 vs. 酒精、汽車 vs. 慢車、第二次 vs. 第一次）
- SOP 只是比條文更嚴格或更詳細的實務作法，並未違反條文
- 需要其他未提供的條文才能判斷

SOP 原句：{sop_text}
條文出處：{law_cite}
條文原句：{law_text}
原判斷：{explanation}

<完整條文>
{law_all}
</完整條文>

只輸出 JSON：{{"valid": true 或 false, "reason": "一句話"}}"""


async def confirm(issue, law_all) -> bool:
    raw = await llm.complete([{"role": "user", "content": VERIFY.format(law_all=law_all, **{
        k: issue.get(k, "") for k in ("sop_text", "law_cite", "law_text", "explanation")})}],
        max_tokens=8192, temperature=0)
    return bool(parse_json(raw).get("valid"))


def norm(s: str) -> str:
    return re.sub(r"[\s「」『』，。、；：（）()]", "", s or "")


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


async def main():
    as_of = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else config.TODAY
    INDEX.load()
    out = {}
    for sid, s in sop.all_sops().items():
        results = await asyncio.gather(*[check_step(s, st, as_of) for st in s["steps"]])
        issues = [i for r in results for i in r]
        out[sid] = {"checked_at": datetime.now().isoformat(timespec="seconds"), "model": config.LLM_MODEL,
                    "as_of": as_of.isoformat(), "issues": issues}
        print(sid, len(issues), "issues")
        for i in issues:
            print(" -", i["step_title"], "|", i["explanation"])
    json.dump(out, open(config.PROCESSED / "sop_check.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
