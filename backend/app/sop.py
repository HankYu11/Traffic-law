"""SOP 流程（checklist）與「SOP vs 現行法規」一致性檢查結果。"""
from __future__ import annotations

import json
from functools import lru_cache

import yaml

from . import config


@lru_cache
def all_sops() -> dict[str, dict]:
    out = {}
    for f in sorted((config.DATA / "sop").glob("*.yaml")):
        s = yaml.safe_load(open(f, encoding="utf-8"))
        out[s["id"]] = s
    return out


@lru_cache
def checks() -> dict:
    f = config.PROCESSED / "sop_check.json"
    return json.load(open(f, encoding="utf-8")) if f.exists() else {}


@lru_cache
def reviews() -> list[dict]:
    f = config.DATA / "sop_review.yaml"
    return (yaml.safe_load(open(f, encoding="utf-8")) or {}).get("reviews", []) if f.exists() else []


STATUS = {"confirmed": "法規室已確認", "corrected": "法規室已修正說明", "rejected": "法規室已排除"}


def _apply_review(sop_id: str, issue: dict) -> dict:
    for r in reviews():
        if r["sop"] == sop_id and r["step_id"] == issue["step_id"] and (
            r["match"] in issue.get("sop_text", "") or r["match"] in issue.get("explanation", "")
        ):
            out = dict(issue, decision=r["decision"], status=STATUS[r["decision"]], reviewer=r.get("reviewer"),
                       reviewed_at=r.get("reviewed_at"), ai_explanation=issue.get("explanation"))
            for k in ("explanation", "law_cite", "law_text", "ai_note"):
                if r.get(k):
                    out[k] = r[k]
            return out
    return dict(issue, decision="pending")


def all_issues(sop_id: str) -> list[dict]:
    return [_apply_review(sop_id, i) for i in checks().get(sop_id, {}).get("issues", [])]


def issues_for_step(sop_id: str, step_id: str, include_rejected: bool = False) -> list[dict]:
    return [i for i in all_issues(sop_id) if i["step_id"] == step_id
            and (include_rejected or i["decision"] != "rejected")]


def get(sop_id: str) -> dict | None:
    s = all_sops().get(sop_id)
    if not s:
        return None
    s = dict(s)
    c = checks().get(sop_id, {})
    s["check"] = {"checked_at": c.get("checked_at"), "model": c.get("model"), "as_of": c.get("as_of")}
    s["steps"] = [dict(st, issues=issues_for_step(sop_id, st["id"], include_rejected=True)) for st in s["steps"]]
    return s
