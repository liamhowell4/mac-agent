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
        expand_domains: int = 0,
    ):
        self.embed_model = embed_model
        # Domain expansion: after top-k, also offer every tool from the domains of the
        # top-m hits ("calendar.update_event ranked high → open the calendar toolbox").
        # Fixes the structural chain miss: a user query ("move my 2pm") can't lexically
        # reach mid-chain tools (calendar.list_events). Offline sweep on the dev set:
        # plain k recall plateaus at 0.735; top-8 + domains-of-top-5 hits 0.882 (~22 tools).
        self.expand_domains = expand_domains
        suffix = f"+dom{expand_domains}" if expand_domains else ""
        self.name = f"embedding:{embed_model}{suffix}"
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
        order = np.argsort(-sims)
        by_name = {t.name: t for t in catalog}
        selected = [by_name[self._names[i]] for i in order[:k]]
        if self.expand_domains:
            domains = {t.domain for t in selected[: self.expand_domains]}
            chosen = {t.name for t in selected}
            # keep ranked order for the expansion so the offered list stays stable
            for i in order[k:]:
                t = by_name[self._names[i]]
                if t.domain in domains and t.name not in chosen:
                    selected.append(t)
                    chosen.add(t.name)
        return selected
