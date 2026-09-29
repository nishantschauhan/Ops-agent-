#!/usr/bin/env python3
"""Lieferblick Knowledge-Base MCP Server (read-only, semantic search).

Loads a fake policy handbook (data/policies.json, produced by seed_data.py),
splits each document into paragraph-level passages, and builds an in-memory
TF-IDF index over them. `search_docs` returns the top-matching passages with
their exact source citation, so the agent can never answer a policy question
without a traceable reference.

Run standalone (e.g. from Claude Desktop):
    KB_API_KEY=dev-kb-key python servers/mcp_knowledge.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from common.auth import require_api_key
from common.paths import data_dir

require_api_key("KB")

mcp = FastMCP("lieferblick-knowledge")

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

_STATE: dict = {}


class PassageResult(BaseModel):
    doc_id: str
    title: str
    source: str
    passage: str
    score: float


class PolicySummary(BaseModel):
    doc_id: str
    title: str
    category: str
    source: str


def _build_index() -> None:
    policies_path = data_dir() / "policies.json"
    if not policies_path.exists():
        raise RuntimeError(f"Policy corpus not found at {policies_path}. Run `python seed_data.py` first.")
    docs = json.loads(policies_path.read_text(encoding="utf-8"))

    passages = []
    for d in docs:
        for i, para in enumerate(p.strip() for p in d["text"].split("\n\n")):
            if not para:
                continue
            passages.append(
                {
                    "doc_id": d["doc_id"],
                    "title": d["title"],
                    "category": d["category"],
                    "source": d["source"],
                    "para_idx": i,
                    "text": para,
                }
            )

    corpus_texts = [f"{p['title']}. {p['text']}" for p in passages]
    vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2))
    matrix = vectorizer.fit_transform(corpus_texts)

    _STATE["docs"] = docs
    _STATE["passages"] = passages
    _STATE["vectorizer"] = vectorizer
    _STATE["matrix"] = matrix


_build_index()


@mcp.tool(annotations=READ_ONLY)
def search_docs(query: str, top_k: int = 3) -> dict:
    """Semantic (TF-IDF) search over Lieferblick's internal policy handbook.

    Returns the most relevant passages, each with the exact document title
    and citation `source` field. ALWAYS cite the `source` field verbatim
    when answering a question using these results — never state a policy
    without it.
    """
    top_k = max(1, min(top_k, 10))
    qvec = _STATE["vectorizer"].transform([query])
    scores = cosine_similarity(qvec, _STATE["matrix"])[0]
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    results = []
    for i in ranked:
        if scores[i] <= 0:
            continue
        p = _STATE["passages"][i]
        results.append(
            PassageResult(
                doc_id=p["doc_id"],
                title=p["title"],
                source=p["source"],
                passage=p["text"],
                score=round(float(scores[i]), 4),
            ).model_dump()
        )

    if not results:
        return {"results": [], "message": "No relevant passages found for this query."}
    return {"results": results}


@mcp.tool(annotations=READ_ONLY)
def list_policies() -> dict:
    """List every policy document in the knowledge base with its category and citation source."""
    return {
        "policies": [
            PolicySummary(doc_id=d["doc_id"], title=d["title"], category=d["category"], source=d["source"]).model_dump()
            for d in _STATE["docs"]
        ]
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
