from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import answer, config, db, sop
from .index import INDEX, status

app = FastAPI(title="警政法規知識庫 Chatbot（實證 Demo）")

DOMAINS = [
    {"id": "traffic", "name": "交通執法", "desc": "道交條例、道路交通安全規則、裁罰細則、事故處理"},
    {"id": "dui", "name": "酒（毒）駕", "desc": "酒駕取締程序、拒測、移送法辦"},
]


@app.on_event("startup")
def startup():
    db.init()
    INDEX.load()


def parse_date(s: str | None) -> date:
    return date.fromisoformat(s) if s else config.TODAY


class AskReq(BaseModel):
    question: str
    domain: str | None = None
    as_of: str | None = None
    history: list[dict] | None = None


@app.get("/api/meta")
def meta():
    import yaml

    m = json.load(open(config.PROCESSED / "corpus_meta.json", encoding="utf-8"))
    cy = yaml.safe_load(open(config.DATA / "corpus.yaml", encoding="utf-8"))
    amendments = json.loads(json.dumps(cy.get("amendments", []), default=str))
    return {"today": config.TODAY.isoformat(), "domains": DOMAINS, "corpus": m, "amendments": amendments,
            "llm": {"model": config.LLM_MODEL, "endpoint": config.LLM_BASE_URL.split("/v1")[0]},
            "embedding": config.EMBED_MODEL, "embed_stats": getattr(INDEX, "embed_stats", {})}


@app.post("/api/ask")
async def ask(req: AskReq):
    as_of = parse_date(req.as_of)

    async def gen():
        sources = []
        try:
            async for ev in answer.ask_stream(req.question, as_of, req.domain, req.history):
                if ev["type"] == "sources":
                    sources = ev["sources"]
                if ev["type"] == "done":
                    ev["query_id"] = db.log_query(req.question, req.domain, as_of.isoformat(), "rag", ev, sources)
                yield answer.dumps(ev)
        except Exception as e:  # noqa: BLE001
            yield answer.dumps({"type": "error", "message": str(e)[:300]})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/baseline")
async def baseline(req: AskReq):
    """對照組：同一個模型、不給資料。用來說明為什麼需要 RAG＋引用驗證。"""
    text = await answer.ask_baseline(req.question)
    return {"answer": text}


@app.get("/api/search")
def quick_search(q: str, domain: str | None = None, as_of: str | None = None):
    d = parse_date(as_of)
    hits = INDEX.quick_search(q, d, domain)
    return {"results": [answer.hit_json(h, i, d) for i, h in enumerate(hits, start=1)]}


@app.get("/api/article/{pcode}/{article_no}")
def article(pcode: str, article_no: str, as_of: str | None = None):
    d = parse_date(as_of)
    chunks = INDEX.article_versions(pcode, article_no)
    if not chunks:
        raise HTTPException(404)
    versions: dict[str, dict] = {}
    for c in chunks:
        v = versions.setdefault(c["version_label"], {"version_label": c["version_label"], "paragraphs": [],
                                                      "law_name": c["law_name"], "article_label": c["article_label"],
                                                      "url": c["url"]})
        v["paragraphs"].append({"paragraph": c["paragraph"], "text": c["text"], "status": status(c, d),
                                "effective_from": c["effective_from"], "effective_to": c["effective_to"],
                                "note": c.get("note", "")})
    return {"versions": list(versions.values()), "as_of": d.isoformat()}


@app.get("/api/sops")
def sops():
    return [{"id": s["id"], "title": s["title"], "domains": s["domains"], "version_label": s["version_label"],
             "steps": len(s["steps"]), "issues": len([i for i in sop.all_issues(s["id"]) if i["decision"] != "rejected"])}
            for s in sop.all_sops().values()]


@app.get("/api/sop/{sop_id}")
def sop_detail(sop_id: str, as_of: str | None = None):
    s = sop.get(sop_id)
    if not s:
        raise HTTPException(404)
    d = parse_date(as_of)
    # 帶出每一步法源的現行條文
    for st in s["steps"]:
        refs = []
        for ref in st.get("legal_basis", []) or []:
            cs = [c for c in INDEX.article_versions(ref["pcode"], ref["article"]) if status(c, d) == "in_force"]
            if ref.get("paragraph"):
                cs = [c for c in cs if (c["paragraph"] or 1) == ref["paragraph"]]
            if cs:
                refs.append({"cite": cs[0]["cite"] if ref.get("paragraph") else cs[0]["law_name"] + cs[0]["article_label"],
                             "pcode": ref["pcode"], "article_no": ref["article"], "paragraph": ref.get("paragraph"),
                             "text": "\n".join(c["text"] for c in cs), "version_label": cs[0]["version_label"]})
        st["legal_refs"] = refs
    return s


class FeedbackReq(BaseModel):
    query_id: int
    rating: int  # 1 / -1
    comment: str = ""
    needs_human: bool = False


@app.post("/api/feedback")
def feedback(req: FeedbackReq):
    db.add_feedback(req.query_id, req.rating, req.comment, req.needs_human)
    return {"ok": True}


@app.get("/api/history")
def history(q: str | None = None, limit: int = 50):
    rows = db.history(limit, q)
    for r in rows:
        r["verification"] = json.loads(r["verification"] or "{}")
        r["sources"] = json.loads(r["sources"] or "[]")
    return rows


@app.get("/api/history/{qid}")
def history_item(qid: int):
    r = db.get_query(qid)
    if not r:
        raise HTTPException(404)
    r["verification"] = json.loads(r["verification"] or "{}")
    r["sources"] = json.loads(r["sources"] or "[]")
    return r


@app.get("/api/stats")
def stats():
    return db.stats()


@app.get("/api/eval")
def eval_report():
    f = config.ROOT / "eval" / "reports" / "latest.json"
    if not f.exists():
        return {"available": False}
    return {"available": True, **json.load(open(f, encoding="utf-8"))}


@app.get("/api/sop-file/{sop_id}")
def sop_file(sop_id: str):
    s = sop.all_sops().get(sop_id)
    if not s or not s.get("source_file"):
        raise HTTPException(404)
    return FileResponse(config.ROOT / s["source_file"], media_type="application/pdf")


# 前端（build 後的靜態檔）
DIST = config.ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
