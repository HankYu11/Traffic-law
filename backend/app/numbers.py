"""把中文數字、金額、濃度正規化成數值，用來檢查「答案中的數字是否出現在引用條文裡」。"""
from __future__ import annotations

import re

DIG = {"零": 0, "〇": 0, "○": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6,
       "七": 7, "八": 8, "九": 9}
UNIT = {"十": 10, "百": 100, "千": 1000}
BIG = {"萬": 10_000, "億": 100_000_000}

CN_NUM_RE = re.compile(r"[零〇○一二兩三四五六七八九十百千萬億]+(?:點[零〇○一二三四五六七八九]+)?")
AR_NUM_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(萬|億)?")


def cn_int(s: str) -> int:
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in DIG:
            num = DIG[ch]
        elif ch in UNIT:
            section += (num or 1) * UNIT[ch]
            num = 0
        elif ch in BIG:
            section += num
            total += (section or 1) * BIG[ch]
            section, num = 0, 0
    return total + section + num


def cn_value(s: str) -> float:
    if "點" in s:
        a, b = s.split("點", 1)
        return cn_int(a) + float("0." + "".join(str(DIG[c]) for c in b))
    return cn_int(s)


def values(text: str) -> set[float]:
    """抽出文字中所有「有意義」的數值（≥ 0 的金額、期間、濃度）。"""
    out: set[float] = set()
    for m in CN_NUM_RE.finditer(text):
        s = m.group(0)
        nxt = text[m.end(): m.end() + 1]
        if nxt == "、":  # 「一、」「二、」等款次編號
            continue
        try:
            out.add(round(cn_value(s), 4))
        except (KeyError, ValueError):
            pass
    for m in AR_NUM_RE.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= BIG[m.group(2)]
        out.add(round(v, 4))
    return out


# 答案中需要檢查的數字型態：金額、濃度、期間、百分比
CLAIM_RE = re.compile(
    r"(?:新臺幣|新台幣|NT\$)?\s*([\d,\.零〇○一二兩三四五六七八九十百千萬點]+)\s*"
    r"(元|萬元|毫克|年|個月|日|天|小時|分鐘|%|％)"
)


def claims(answer: str) -> list[tuple[str, float]]:
    out = []
    for m in CLAIM_RE.finditer(answer):
        raw, unit = m.group(1).strip(), m.group(2)
        try:
            if re.fullmatch(r"[\d,\.]+萬?", raw):
                v = float(raw.replace(",", "").replace("萬", "")) * (10_000 if "萬" in raw else 1)
            else:
                v = cn_value("".join("零一二三四五六七八九"[int(ch)] if ch.isdigit() else ch for ch in raw))
        except (KeyError, ValueError):
            continue
        if unit == "萬元":
            v *= 10_000
        out.append((m.group(0).strip(), round(v, 4)))
    return out
