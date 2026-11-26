"""混合檢索：BM25（中文字元 bigram）＋ 向量（EmbeddingGemma）＋ 條號精確比對，以 RRF 融合。

版本控管：每個片段有 effective_from / effective_to，檢索時依「查詢基準日」過濾。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np

from . import config

CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in CN_NUM:
            cur = CN_NUM[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur


ART_Q_RE = re.compile(r"第?\s*([0-9一二三四五六七八九十百]+)\s*條(?:之\s*([0-9一二三四五六七八九十]+))?")


def article_refs(q: str) -> list[str]:
    """從問題抓出條號：'35條'、'第三十五條'、'第7條之2' -> ['35', '7-2']"""
    out = []
    for m in ART_Q_RE.finditer(q):
        a = cn_to_int(m.group(1))
        if a is None:
            continue
        s = str(a)
        if m.group(2):
            b = cn_to_int(m.group(2))
            if b is not None:
                s += f"-{b}"
        out.append(s)
    return out


def tokens(text: str) -> list[str]:
    text = re.sub(r"\s+", "", text)
    out = []
    for seg in re.findall(r"[一-鿿]+|[A-Za-z]+|\d+(?:\.\d+)?", text):
        if re.match(r"[一-鿿]", seg):
            out += [seg[i : i + 2] for i in range(len(seg) - 1)] or [seg]
        else:
            out.append(seg.lower())
    return out


def load_synonyms() -> dict[str, list[str]]:
    import yaml

    f = config.DATA / "synonyms.yaml"
    return yaml.safe_load(open(f, encoding="utf-8")) or {} if f.exists() else {}


SYNONYMS = load_synonyms()


def expand_query(q: str) -> tuple[str, list[str]]:
    """員警口語 → 法條用語。回傳（擴充後查詢, 套用的擴充詞）。"""
    added: list[str] = []
    for k in sorted(SYNONYMS, key=len, reverse=True):
        if k in q:
            for v in SYNONYMS[k]:
                if v not in q and v not in added:
                    added.append(v)
    return (q + " " + " ".join(added)) if added else q, added


@dataclass
class Hit:
    chunk: dict
    score: float
    dense: float
    bm25: float
    exact: bool


def in_force(c: dict, as_of: date) -> bool:
    f, t = c.get("effective_from"), c.get("effective_to")
    if f and date.fromisoformat(f) > as_of:
        return False
    if t and date.fromisoformat(t) < as_of:
        return False
    return True


def status(c: dict, as_of: date) -> str:
    f, t = c.get("effective_from"), c.get("effective_to")
    if f and date.fromisoformat(f) > as_of:
        return "pending"
    if t and date.fromisoformat(t) < as_of:
        return "superseded"
    return "in_force"


class Index:
    def __init__(self):
        self.chunks: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.vecs: np.ndarray | None = None
        self._embedder = None

    # ---------- build / load ----------
    def load(self):
        self.chunks = [json.loads(l) for l in open(config.PROCESSED / "chunks.jsonl", encoding="utf-8")]
        self.by_id = {c["id"]: c for c in self.chunks}
        order = []
        for c in self.chunks:
            if c["pcode"] not in order:
                order.append(c["pcode"])
        self.law_rank = {p: i for i, p in enumerate(order)}
        self.law_names = {c["law_name"] for c in self.chunks if c["source_type"] == "law"}
        self._build_bm25()
        self._load_or_build_vectors()

    def _build_bm25(self):
        """倒排索引版 BM25（毫秒級）。"""
        k1, b = 1.5, 0.75
        df = Counter()
        tfs = []
        for c in self.chunks:
            toks = tokens(c["law_name"] + c["article_label"] + c["chapter"] + c.get("index_text", c["text"]))
            tf = Counter(toks)
            tfs.append((tf, len(toks)))
            df.update(tf.keys())
        n = len(self.chunks)
        avgdl = sum(l for _, l in tfs) / max(n, 1)
        idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        post: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
        for i, (tf, dl) in enumerate(tfs):
            for t, f in tf.items():
                ids, ws = post[t]
                ids.append(i)
                ws.append(idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl)))
        self.postings = {t: (np.array(ids), np.array(ws)) for t, (ids, ws) in post.items()}

    def bm25(self, q: str) -> np.ndarray:
        scores = np.zeros(len(self.chunks))
        for t in tokens(q):
            p = self.postings.get(t)
            if p is not None:
                scores[p[0]] += p[1]
        return scores

    @property
    def embedder(self):
        if self._embedder is None:
            from fastembed import TextEmbedding

            self._embedder = TextEmbedding(config.EMBED_MODEL, cache_dir=config.MODEL_CACHE)
        return self._embedder

    @staticmethod
    def _doc_text(c: dict) -> str:
        return f"title: {c['law_name']} {c['article_label']} | text: {c.get('index_text', c['text'])}"

    def _load_or_build_vectors(self):
        """增量更新：只對內容有變動的片段重新計算向量。"""
        cache_f = config.PROCESSED / "vectors.npz"
        cached: dict[str, np.ndarray] = {}
        if cache_f.exists():
            z = np.load(cache_f, allow_pickle=True)
            cached = dict(zip(z["keys"].tolist(), z["vecs"]))
        keys = [hashlib.sha1(self._doc_text(c).encode()).hexdigest() for c in self.chunks]
        missing = [i for i, k in enumerate(keys) if k not in cached]
        if missing:
            print(f"[index] embedding {len(missing)} new/changed chunks (cached {len(keys) - len(missing)})")
            new = list(self.embedder.embed([self._doc_text(self.chunks[i]) for i in missing], batch_size=16))
            for i, v in zip(missing, new):
                cached[keys[i]] = np.asarray(v, dtype=np.float32)
            cur = {k: cached[k] for k in keys}  # 只保留目前片段的向量，避免快取越長越大
            np.savez(cache_f, keys=np.array(list(cur.keys())), vecs=np.stack(list(cur.values())))
        v = np.stack([cached[k] for k in keys])
        self.vecs = v / np.linalg.norm(v, axis=1, keepdims=True)
        self.embed_stats = {"total": len(keys), "recomputed": len(missing)}

    @lru_cache(maxsize=512)
    def embed_query(self, q: str) -> np.ndarray:
        v = np.asarray(next(iter(self.embedder.embed([f"task: search result | query: {q}"]))), dtype=np.float32)
        return v / np.linalg.norm(v)

    # ---------- search ----------
    def search(self, q: str, as_of: date, domain: str | None = None, k: int = config.TOP_K,
               include_sop: bool = True, qvec: np.ndarray | None = None) -> list[Hit]:
        q, _ = expand_query(q)
        dense = self.vecs @ (qvec if qvec is not None else self.embed_query(q))
        bm = self.bm25(q)
        refs = set(article_refs(q))
        mask = np.array([
            in_force(c, as_of)
            and (domain is None or domain in c["domains"])
            and (include_sop or c["source_type"] != "sop")
            for c in self.chunks
        ])
        exact = np.array([c["source_type"] == "law" and c["article_no"] in refs and
                          (not any(n in q for n in ["規則", "細則", "辦法", "刑法", "訴訟法", "職權"]) or
                           c["law_name"][:4] in q)
                          for c in self.chunks])

        rrf = defaultdict(float)
        for arr in (dense, bm):
            order = [i for i in np.argsort(-arr) if mask[i]][:50]
            for r, i in enumerate(order):
                rrf[i] += 1 / (60 + r)
        for i in np.where(exact & mask)[0]:
            rrf[i] += 1 / 50  # 條號精確比對加權
        top = sorted(rrf.items(), key=lambda x: -x[1])[:k]
        return [Hit(self.chunks[i], s, float(dense[i]), float(bm[i]), bool(exact[i])) for i, s in top]

    def quick_search(self, q: str, as_of: date, domain: str | None = None, k: int = 20,
                     pcode: str | None = None) -> list[Hit]:
        """外勤快速查詢：不經 LLM，條號／關鍵字直接比對，毫秒級回應。"""
        bm = self.bm25(expand_query(q)[0])
        refs = set(article_refs(q))
        named = {n for n in self.law_names if n[:4] in q or (n == "道路交通管理處罰條例" and "道交" in q)}
        hits = []
        for i, c in enumerate(self.chunks):
            if c["source_type"] != "law" or not in_force(c, as_of):
                continue
            if domain and domain not in c["domains"]:
                continue
            if pcode and c["pcode"] != pcode:
                continue
            ex = c["article_no"] in refs and (not named or c["law_name"] in named)
            s = bm[i] + (100 if ex else 0)
            if s > 0:
                hits.append(Hit(c, s, 0.0, float(bm[i]), ex))
        # 條號命中時：依法規位階／收錄順序（道交條例優先），再依項次排列
        hits.sort(key=lambda h: (not h.exact, self.law_rank.get(h.chunk["pcode"], 99) if h.exact else 0,
                                 (h.chunk["paragraph"] or 0) if h.exact else 0, -h.score))
        return hits[:k]

    def current(self, pcode: str, article_no: str, as_of: date, paragraph: int | None = None) -> list[dict]:
        cs = [c for c in self.article_versions(pcode, article_no) if in_force(c, as_of)]
        if paragraph:
            cs = [c for c in cs if (c["paragraph"] or 1) == paragraph]
        return cs

    def article_versions(self, pcode: str, article_no: str) -> list[dict]:
        return [c for c in self.chunks if c["pcode"] == pcode and c["article_no"] == article_no]


INDEX = Index()
