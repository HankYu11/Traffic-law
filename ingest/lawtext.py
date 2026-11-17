"""法規文字處理共用函式：條號正規化、分項（paragraph）切分、硬換行還原。"""
import re

ITEM_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）|\([一二三四五六七八九十百]+\)|\d+\.|\d+、|（\d+）)"
)
ARTICLE_NO_RE = re.compile(r"第\s*([\d]+(?:\s*-\s*\d+)?)\s*條")


def norm_article_no(raw: str) -> str:
    """'第 185-3 條' -> '185-3'"""
    m = ARTICLE_NO_RE.search(raw or "")
    return m.group(1).replace(" ", "") if m else ""


def article_label(no: str) -> str:
    return f"第{no}條" if "-" not in no else f"第{no.split('-')[0]}條之{no.split('-')[1]}"


def is_item(line: str) -> bool:
    return bool(ITEM_RE.match(line))


def split_paragraphs(content: str) -> list[str]:
    """把條文切成「項」。款（一、二、…）併入所屬的項。

    全國法規資料庫 JSON 的條文，每一項或每一款各佔一行（以 \\r\\n 分隔）。
    """
    lines = [l.strip() for l in content.replace("\r\n", "\n").split("\n") if l.strip()]
    paras: list[list[str]] = []
    for line in lines:
        if paras and is_item(line):
            paras[-1].append(line)
        else:
            paras.append([line])
    return ["\n".join(p) for p in paras]


def unwrap_hard_wrapped(text: str) -> str:
    """把網頁版（text-pre，固定寬度硬換行）的條文還原成「一項或一款一行」。"""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if not out:
            out.append(line)
            continue
        prev = out[-1]
        if prev.endswith(("。", "：", "；")) or is_item(line):
            out.append(line)
        else:
            out[-1] = prev + line
    return "\n".join(out)


# ---------------- 條文內部參照展開 ----------------
CN = "一二三四五六七八九十"
REF_RE = re.compile(
    r"(?<![條之])(第([一二三四五六七八九十]+)項(?:第([一二三四五六七八九十]+)款)?|前項(?:第([一二三四五六七八九十]+)款)?)"
)


def _cn(s: str) -> int:
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + CN.index(s[1]) + 1
    if "十" in s:
        a, b = s.split("十")
        return (CN.index(a) + 1) * 10 + (CN.index(b) + 1 if b else 0)
    return CN.index(s) + 1


def _lead(p: str, n: int = 28) -> str:
    first = p.split("\n")[0]
    first = re.split(r"[，；：]", first)[0] if len(first) > n else first
    return first[:n] + ("…" if len(first) > n else "")


def _items(p: str) -> list[str]:
    return [l for l in p.split("\n")[1:] if is_item(l)]


def _para_summary(p: str) -> str:
    """項的摘要：開頭一句＋各款重點，讓「違反第一項」這類參照讀得懂。"""
    items = _items(p)
    s = _lead(p)
    if items:
        s += "：" + "；".join(re.sub(r"^\s*[一二三四五六七八九十]+、", "", i)[:18] for i in items)
    return s


def annotate_refs(paras: list[str], idx: int, items_only: bool = False) -> str:
    """把第 idx 項（1-based）中指向同條其他項／款的參照展開成〔…〕。"""
    text = paras[idx - 1]
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        whole = m.group(1)
        if whole in seen:  # 同一參照只展開第一次
            return whole
        seen.add(whole)
        try:
            if whole.startswith("前項"):
                pn, item = idx - 1, m.group(4)
            else:
                pn, item = _cn(m.group(2)), m.group(3)
        except (ValueError, IndexError):
            return whole
        if not (1 <= pn <= len(paras)) or pn == idx:
            return whole
        target = paras[pn - 1]
        if items_only and not item:
            return whole
        if item:
            items = _items(target)
            k = _cn(item)
            if not (1 <= k <= len(items)):
                return whole
            body = re.sub(r"^\s*[一二三四五六七八九十]+、", "", items[k - 1])
            return f"{whole}〔{body[:40]}〕"
        return f"{whole}〔{_para_summary(target)}〕"

    # 第X項第Y款後面若緊接「或第Z款」也一起處理：先展開主要參照即可
    return REF_RE.sub(repl, text)
