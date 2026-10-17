"""Replaceable retrieval tool: Chinese bigram BM25 + optional existing HTTP RAG."""
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import httpx


def tokenize(text: str):
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(span)
        words.extend(span[i:i + 2] for i in range(len(span) - 1))
    return words


class Retriever:
    def __init__(self, settings):
        self.settings = settings
        roots = [Path(__file__).parent / "knowledge"]
        if settings.rag_dir:
            roots.append(Path(settings.rag_dir))
        self.documents = []
        for root in roots:
            for path in sorted(root.rglob("*.md")):
                text = path.read_text(encoding="utf-8")[:100000]
                # Bounded chunks keep retrieval from flooding the builder context.
                for start in range(0, len(text), 1800):
                    chunk = text[start:start + 2000]
                    self.documents.append({
                        "id": hashlib.sha256((str(path.relative_to(root)) + str(start) + chunk).encode()).hexdigest()[:12],
                        "title": text.splitlines()[0].lstrip("# "), "content": chunk,
                        "source": str(path.relative_to(root)), "origin": "local",
                    })
        self.counts = [Counter(tokenize(d["content"])) for d in self.documents]
        self.df = Counter(t for c in self.counts for t in c)
        self.avg_length = sum(sum(c.values()) for c in self.counts) / max(1, len(self.counts))

    def local_search(self, query: str, top_k=4):
        terms = set(tokenize(query))
        scored = []
        for doc, counts in zip(self.documents, self.counts):
            length = sum(counts.values())
            score = 0.0
            for term in terms:
                tf = counts[term]
                idf = math.log(1 + (len(self.counts) - self.df[term] + .5) / (self.df[term] + .5))
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / max(1, self.avg_length)))
            if score > 0:
                scored.append({**doc, "score": round(score, 4)})
        return sorted(scored, key=lambda d: d["score"], reverse=True)[:top_k]

    async def search(self, query: str, top_k=4):
        warnings = []
        local = self.local_search(query, top_k)
        remote = []
        if self.settings.rag_url:
            headers = {"Authorization": "Bearer " + self.settings.rag_token} if self.settings.rag_token else {}
            try:
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    response = await client.post(self.settings.rag_url, json={"query": query, "top_k": top_k}, headers=headers)
                    response.raise_for_status()
                    documents = response.json()["documents"]
                    if not isinstance(documents, list):
                        raise ValueError("documents must be an array")
                    for d in documents[:top_k]:
                        remote.append({"id": str(d["id"])[:100], "title": str(d["title"])[:160],
                                       "content": str(d["content"])[:2500], "source": str(d.get("source", "external"))[:500],
                                       "origin": "external"})
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                # Do not put remote URLs/tokens or response bodies in public logs.
                warnings.append("外部 RAG 不可用，已回退本地知识库：" + type(exc).__name__)
        return {"query": query, "documents": (remote + local)[:top_k], "warnings": warnings}
