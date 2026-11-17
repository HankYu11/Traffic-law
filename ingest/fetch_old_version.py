"""從全國法規資料庫「舊法規內容」頁抓前一版條文（示範版本控管用）。

用法：python ingest/fetch_old_version.py K0040012
輸出：data/processed/old_versions/<pcode>.json
"""
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lawtext import norm_article_no, unwrap_hard_wrapped  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def fetch(pcode: str) -> str:
    cache = ROOT / "data" / "raw" / f"oldver_{pcode}.html"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    url = f"https://law.moj.gov.tw/LawClass/LawOldVer.aspx?pcode={pcode}"
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8")
    cache.write_text(text, encoding="utf-8")
    return text


def parse(page: str) -> dict:
    m = re.search(r"修正日期：\s*</th>\s*<td>\s*([^<]+)<", page) or re.search(r"修正日期：([^<]+)<", page)
    label = html.unescape(m.group(1)).strip() if m else ""
    arts = {}
    for no_raw, body in re.findall(
        r'<div class="col-no">(.*?)</div><div class="col-data text-pre">(.*?)</div>', page, flags=re.S
    ):
        no = norm_article_no(html.unescape(re.sub(r"<[^>]+>", "", no_raw)))
        if not no:
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", body))
        arts[no] = unwrap_hard_wrapped(text)
    return {"modified_label": label, "articles": arts}


if __name__ == "__main__":
    pcode = sys.argv[1] if len(sys.argv) > 1 else "K0040012"
    data = parse(fetch(pcode))
    out = ROOT / "data" / "processed" / "old_versions"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{pcode}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(pcode, data["modified_label"], len(data["articles"]), "articles")
