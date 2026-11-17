"""把法規＋舊版條文＋SOP 轉成「項」層級的檢索片段（chunks.jsonl）。

每個片段都帶版本 Metadata：version_label / effective_from / effective_to / note。
檢索時依「查詢基準日」過濾，確保不會引用過期或尚未施行的條文。
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lawtext import article_label, norm_article_no, split_paragraphs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"


def load_dump() -> dict:
    laws = {}
    for f in ["ChLaw.json", "ChOrder.json"]:
        d = json.load(open(RAW / f, encoding="utf-8-sig"))
        for l in d["Laws"]:
            pcode = l["LawURL"].split("pcode=")[-1]
            laws[pcode] = l
    return laws


def fmt_date(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if s and len(s) == 8 and s != "99991231" else ""


def law_chunks(law: dict, cfg: dict, amend: dict | None, old: dict | None):
    pcode = cfg["pcode"]
    name = cfg.get("display_name", cfg["name"])
    only = set(cfg.get("articles") or [])
    modified = fmt_date(law["LawModifiedDate"])
    chapter = ""
    amended_arts = set(amend["articles"]) if amend else set()
    for a in law["LawArticles"]:
        if a["ArticleType"] == "C":
            chapter = a["ArticleContent"].strip()
            continue
        no = norm_article_no(a["ArticleNo"])
        if not no or (only and no not in only):
            continue
        if not a["ArticleContent"].strip() or a["ArticleContent"].strip() in ("（刪除）", "(刪除)"):
            continue
        base = dict(
            source_type="law",
            pcode=pcode,
            law_name=name,
            law_level=law["LawLevel"],
            article_no=no,
            article_label=article_label(no),
            chapter=chapter,
            domains=cfg["domains"],
            url=f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={no}",
            law_modified=modified,
        )
        if no in amended_arts:
            # 新版（施行日可能逐項不同）
            overrides = {
                o["article"]: o for o in amend.get("paragraph_overrides", []) if o["article"] == no
            }
            ov = overrides.get(no)
            yield from emit(
                base, a["ArticleContent"], amend["new_version_label"],
                default_from=amend["effective"], effective_to=None,
                para_from={p: ov["effective"] for p in ov["new_paragraphs"]} if ov else {},
                para_note={p: ov.get("note", "") for p in ov["new_paragraphs"]} if ov else {},
                promulgated=amend["promulgated"], order=amend["order"],
            )
            # 舊版（新版施行日前一天失效）
            if old and no in old["articles"]:
                to = (date.fromisoformat(str(amend["effective"])) - timedelta(days=1)).isoformat()
                yield from emit(
                    base, old["articles"][no], amend["old_version_label"],
                    default_from=None, effective_to=to, para_from={}, para_note={},
                )
        else:
            yield from emit(base, a["ArticleContent"], f"現行（{modified} 異動）", None, None, {}, {})


def emit(base, content, version_label, default_from, effective_to, para_from, para_note, **extra):
    paras = split_paragraphs(content)
    full = "\n".join(paras)
    for i, p in enumerate(paras, start=1):
        multi = len(paras) > 1
        eff_from = para_from.get(i, default_from)
        yield {
            **base,
            "id": f"{base['pcode']}:{base['article_no']}:{i}@{version_label}",
            "paragraph": i if multi else None,
            "cite": base["law_name"] + base["article_label"] + (f"第{i}項" if multi else ""),
            "text": p,
            "article_text": full,
            "version_label": version_label,
            "effective_from": str(eff_from) if eff_from else None,
            "effective_to": str(effective_to) if effective_to else None,
            "note": para_note.get(i, ""),
            **{k: str(v) for k, v in extra.items()},
        }


def sop_chunks():
    for f in sorted((ROOT / "data" / "sop").glob("*.yaml")):
        sop = yaml.safe_load(open(f, encoding="utf-8"))
        for st in sop["steps"]:
            lines = [st["title"]]
            lines += [f"・{x}" for x in st.get("actions", [])]
            if st.get("forms"):
                lines.append("應備表單：" + "、".join(st["forms"]))
            lines += [f"注意：{x}" for x in st.get("cautions", [])]
            yield {
                "id": f"SOP:{sop['id']}:{st['id']}",
                "source_type": "sop",
                "pcode": f"SOP-{sop['id']}",
                "law_name": sop["title"],
                "law_level": "作業程序",
                "article_no": st["id"],
                "article_label": st["title"],
                "chapter": st.get("phase", ""),
                "domains": sop["domains"],
                "url": sop.get("source_url", ""),
                "law_modified": sop.get("version_date", ""),
                "paragraph": None,
                "cite": f"{sop['title']}「{st['title']}」",
                "text": "\n".join(lines),
                "article_text": "\n".join(lines),
                "version_label": sop.get("version_label", ""),
                "effective_from": None,
                "effective_to": None,
                "note": "",
                "sop_id": sop["id"],
                "step_id": st["id"],
                "source_page": st.get("source_page"),
            }


def main():
    cfg = yaml.safe_load(open(ROOT / "data" / "corpus.yaml", encoding="utf-8"))
    dump = load_dump()
    amends = {a["pcode"]: a for a in cfg.get("amendments", [])}
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    meta = []
    with open(OUT / "chunks.jsonl", "w", encoding="utf-8") as fo:
        for lc in cfg["laws"]:
            law = dump[lc["pcode"]]
            oldf = OUT / "old_versions" / f"{lc['pcode']}.json"
            old = json.load(open(oldf, encoding="utf-8")) if oldf.exists() else None
            cnt = 0
            for ch in law_chunks(law, lc, amends.get(lc["pcode"]), old):
                fo.write(json.dumps(ch, ensure_ascii=False) + "\n")
                cnt += 1
            meta.append({
                "pcode": lc["pcode"], "name": lc.get("display_name", lc["name"]),
                "level": law["LawLevel"], "modified": fmt_date(law["LawModifiedDate"]),
                "domains": lc["domains"], "chunks": cnt, "source": "全國法規資料庫 Open API",
                "partial": bool(lc.get("articles")),
            })
            n += cnt
        cnt = 0
        for ch in sop_chunks():
            fo.write(json.dumps(ch, ensure_ascii=False) + "\n")
            cnt += 1
        n += cnt
    json.dump({"documents": meta, "sop_chunks": cnt, "total_chunks": n,
               "dump_updated": json.load(open(RAW / "ChLaw.json", encoding="utf-8-sig"))["UpdateDate"]},
              open(OUT / "corpus_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("chunks:", n, "sop:", cnt)
    for m in meta:
        print(" ", m["name"], m["chunks"])


if __name__ == "__main__":
    main()
