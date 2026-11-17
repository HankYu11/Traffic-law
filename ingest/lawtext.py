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
