"""自動化評估：檢索命中率、回答正確率（LLM 評分＋要點）、引用驗證通過率、拒答正確率，以及「不接知識庫」對照組。

用法：python eval/run_eval.py [--limit N] [--no-baseline]
輸出：eval/reports/latest.json、eval/reports/<timestamp>.json、eval/reports/latest.md
評分模型與回答模型分開（預設 gemini-2.5-flash），避免自己改自己的考卷。
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import answer, config, llm  # noqa: E402
from app.index import INDEX  # noqa: E402

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-2.5-flash")

JUDGE = """你是警察法規考試的閱卷老師。請依「答案要點」判斷考生回答是否正確。

判定規則：
- 每一個要點都必須在回答中出現（意思相同即可，不必逐字），數字（金額、期間、濃度）必須完全正確。
- 回答中若包含與要點或現行法規矛盾的錯誤數字或錯誤法條，判為錯誤。
- 多寫的正確補充資訊不扣分。
- 若要點要求「應回答查無依據」，則考生明確表示查無依據／不在範圍即為正確，編造內容即為錯誤。

問題：{q}
答案要點：
{points}

考生回答：
{a}

只輸出 JSON：{{"correct": true 或 false, "missing": ["缺漏或錯誤的要點"], "reason": "一句話理由"}}"""


async def judge(q, points, a):
    saved = config.LLM_MODEL
    config.LLM_MODEL = JUDGE_MODEL
    try:
        raw = await llm.complete([{"role": "user", "content": JUDGE.format(
            q=q, points="\n".join(f"- {p}" for p in points), a=a or "（無回答）")}], max_tokens=8192, temperature=0)
    finally:
        config.LLM_MODEL = saved
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0)) if m else {"correct": False, "reason": "評分失敗"}
    except json.JSONDecodeError:
        return {"correct": False, "reason": "評分失敗"}


def hit(expected, sources):
    if not expected:
        return True  # 範圍外題目不計檢索
    have = {f"{s['pcode']}:{s['article_no']}" for s in sources}
    return any(e in have for e in expected)


async def run_item(it, defaults, sem, baseline: bool):
    async with sem:
        as_of = date.fromisoformat(it.get("as_of", defaults["as_of"]))
        t0 = time.perf_counter()
        for attempt in range(3):
            try:
                r = await answer.ask(it["q"], as_of)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    r = {"answer": f"（錯誤：{e}）", "sources": [], "verification": {"ok": False}, "refused": False}
                await asyncio.sleep(3)
        latency = round((time.perf_counter() - t0) * 1000)
        refuse_expected = bool(it.get("refuse"))
        if refuse_expected:
            correct = bool(r.get("refused")) or "查無足夠依據" in r.get("answer", "")
            j = {"correct": correct, "reason": "正確拒答" if correct else "應拒答卻作答"}
        else:
            j = await judge(it["q"], it["points"], r.get("answer", ""))
        b = None
        if baseline:
            try:
                ba = await answer.ask_baseline(it["q"] + (f"（以 {as_of.isoformat()} 當天有效的規定回答）" if it.get("as_of") else ""))
                if refuse_expected:
                    b = {"correct": False, "reason": "未拒答（模型直接作答）", "answer": ba}
                else:
                    b = dict(await judge(it["q"], it["points"], ba), answer=ba)
            except Exception as e:  # noqa: BLE001
                b = {"correct": False, "reason": f"錯誤：{e}", "answer": ""}
        res = {
            "id": it["id"], "category": it["category"], "question": it["q"], "as_of": as_of.isoformat(),
            "expected": it["expected"], "points": it["points"],
            "retrieval_hit": hit(it["expected"], r.get("sources", [])),
            "correct": bool(j.get("correct")), "judge_reason": j.get("reason", ""), "missing": j.get("missing", []),
            "verification_ok": bool(r.get("verification", {}).get("ok")), "refused": bool(r.get("refused")),
            "answer": r.get("answer", ""), "sources": [s["cite"] for s in r.get("sources", [])],
            "latency_ms": latency,
            "baseline_correct": (b or {}).get("correct"), "baseline_reason": (b or {}).get("reason"),
            "baseline_answer": (b or {}).get("answer"),
        }
        mark = "✓" if res["correct"] else "✗"
        print(f"{mark} {it['id']} hit={res['retrieval_hit']} verify={res['verification_ok']} "
              f"base={res['baseline_correct']} {latency}ms  {res['judge_reason'][:50]}", flush=True)
        return res


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-baseline", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    spec = yaml.safe_load(open(ROOT / "eval" / "questions.yaml", encoding="utf-8"))
    items = spec["items"]
    if args.only:
        items = [i for i in items if i["id"] in args.only.split(",")]
    if args.limit:
        items = items[: args.limit]
    INDEX.load()
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[run_item(it, spec["defaults"], sem, not args.no_baseline) for it in items])

    n = len(results)
    pct = lambda k: round(100 * sum(1 for r in results if r[k]) / n, 1) if n else 0  # noqa: E731
    answered = [r for r in results if not r["refused"]]
    cats = defaultdict(lambda: {"n": 0, "correct": 0, "retrieval_hit": 0, "baseline_correct": 0})
    for r in results:
        c = cats[r["category"]]
        c["n"] += 1
        c["correct"] += r["correct"]
        c["retrieval_hit"] += r["retrieval_hit"]
        c["baseline_correct"] += bool(r["baseline_correct"])
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.LLM_MODEL, "judge_model": JUDGE_MODEL, "embedding": config.EMBED_MODEL,
        "as_of": spec["defaults"]["as_of"], "n": n,
        "summary": {
            "answer_accuracy": pct("correct"),
            "retrieval_hit_rate": pct("retrieval_hit"),
            "verification_pass_rate": round(100 * sum(r["verification_ok"] for r in answered) / max(len(answered), 1), 1),
            "baseline_accuracy": pct("baseline_correct"),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / max(n, 1)),
        },
        "by_category": [{"category": k, **v} for k, v in cats.items()],
        "items": results,
    }
    out = ROOT / "eval" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for f in (out / "latest.json", out / f"{stamp}.json"):
        json.dump(report, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_md(report, out / "latest.md")
    print(json.dumps(report["summary"], ensure_ascii=False))


def write_md(r, path):
    s = r["summary"]
    lines = [
        "# 評估報告", "",
        f"- 執行時間：{r['run_at']}",
        f"- 回答模型：{r['model']}；評分模型：{r['judge_model']}；向量模型：{r['embedding']}",
        f"- 題數：{r['n']}", "",
        "| 指標 | 數值 |", "|---|---|",
        f"| 回答正確率 | **{s['answer_accuracy']}%** |",
        f"| 檢索命中率 | {s['retrieval_hit_rate']}% |",
        f"| 引用驗證通過率 | {s['verification_pass_rate']}% |",
        f"| 對照：同模型不接知識庫 | {s['baseline_accuracy']}% |",
        f"| 平均回應時間 | {s['avg_latency_ms'] / 1000:.1f} 秒 |", "",
        "| 題型 | 題數 | 本系統 | 不接知識庫 |", "|---|---|---|---|",
    ]
    for c in r["by_category"]:
        lines.append(f"| {c['category']} | {c['n']} | {c['correct']}/{c['n']} | {c['baseline_correct']}/{c['n']} |")
    lines += ["", "## 錯題", ""]
    for it in r["items"]:
        if not it["correct"]:
            lines.append(f"- **{it['id']}** {it['question']}：{it['judge_reason']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
