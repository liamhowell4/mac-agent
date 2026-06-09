"""Embedding retriever — top-k tools by cosine similarity to the query.

Embeds each tool's "name: description" with a local embed model (nomic-embed-text via
Ollama) and returns the k nearest to the query. Tool embeddings are static, so they're
computed once and cached to disk (keyed by embed model + catalog contents).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import numpy as np

from ..types import ToolSchema

HARD_CAP = 15  # never surface more than this, even if k is larger

# Asymmetric-retrieval task prefixes. Using the right prefix materially improves recall —
# omitting them (the naive baseline) measurably hurts both nomic and e5.
PREFIXES = {
    "e5": ("query: ", "passage: "),
    "nomic": ("search_query: ", "search_document: "),
    "bge": ("Represent this query for retrieving relevant passages: ", ""),
}


def _prefixes_for(model: str) -> tuple[str, str]:
    m = model.lower()
    for key, pair in PREFIXES.items():
        if key in m:
            return pair
    return ("", "")  # unknown model → no prefix


class EmbeddingRetriever:
    def __init__(
        self,
        embed_model: str = "e5",  # was nomic; e5 with query:/passage: prefixes retrieves better
        host: str = "http://localhost:11434",
        cache_dir: Path | None = Path("results/cache"),
    ):
        self.embed_model = embed_model
        self.name = f"embedding:{embed_model}"
        self.host = host.rstrip("/")
        self.cache_dir = cache_dir
        self.q_prefix, self.d_prefix = _prefixes_for(embed_model)
        self._matrix: np.ndarray | None = None
        self._names: list[str] = []

    # ---- embedding ----
    def _embed(self, texts: list[str]) -> np.ndarray:
        resp = httpx.post(
            f"{self.host}/api/embed",
            json={"model": self.embed_model, "input": texts},
            timeout=120.0,
        )
        resp.raise_for_status()
        vecs = np.array(resp.json()["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-9, None)

    def _tool_text(self, t: ToolSchema) -> str:
        return f"{self.d_prefix}{t.name}: {t.description}"

    def _ensure_index(self, catalog: list[ToolSchema]) -> None:
        if self._matrix is not None and self._names == [t.name for t in catalog]:
            return
        key = hashlib.sha256(
            (self.embed_model + "|" + "|".join(self._tool_text(t) for t in catalog)).encode()
        ).hexdigest()[:16]
        cache_fp = self.cache_dir / f"embed_{key}.json" if self.cache_dir else None

        if cache_fp and cache_fp.exists():
            data = json.loads(cache_fp.read_text())
            self._names = data["names"]
            self._matrix = np.array(data["matrix"], dtype=np.float32)
            return

        self._names = [t.name for t in catalog]
        self._matrix = self._embed([self._tool_text(t) for t in catalog])
        if cache_fp:
            cache_fp.parent.mkdir(parents=True, exist_ok=True)
            cache_fp.write_text(json.dumps({"names": self._names, "matrix": self._matrix.tolist()}))

    # ---- retrieval ----
    def select(self, query: str, catalog: list[ToolSchema], k: int) -> list[ToolSchema]:
        self._ensure_index(catalog)
        assert self._matrix is not None
        qv = self._embed([self.q_prefix + query])[0]
        sims = self._matrix @ qv
        k = min(k, HARD_CAP, len(catalog))
        top = np.argsort(-sims)[:k]
        by_name = {t.name: t for t in catalog}
        return [by_name[self._names[i]] for i in top]
