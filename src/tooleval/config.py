"""Run-config loading + cell expansion for the matrix runner.

A config (config/run.example.yaml) expands into the cartesian product of
models × retrieval × decoding × prompts. Each combination is one cell.
"""

from __future__ import annotations

import os
import re
from itertools import product
from pathlib import Path
from typing import Any

import yaml

from .providers.ollama import OllamaProvider
from .retrieval.embedding import EmbeddingRetriever
from .retrieval.passthrough import PassthroughRetriever

_ENV_RE = re.compile(r"\$\{(\w+)\}")


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader (no dependency). Does not overwrite existing env vars."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def subst_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    return value


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


# Providers are cached per spec: Ollama is server-backed (cheap to re-wrap), but
# MLXProvider lazy-loads 4–9GB of weights — a fresh instance per cell would reload them.
_provider_cache: dict[tuple, Any] = {}


def build_provider(spec: dict, host: str, seed: int):
    kind = spec["provider"]
    key = (kind, spec["model"], host, seed)
    if key in _provider_cache:
        return _provider_cache[key]
    if kind == "ollama":
        provider = OllamaProvider(spec["model"], host=host, seed=seed)
    elif kind == "mlx":
        from .providers.mlx import MLXProvider  # noqa: PLC0415 — keeps mlx_lm optional

        provider = MLXProvider(spec["model"], seed=seed)
    else:
        raise NotImplementedError(f"unknown provider kind: {kind!r}")
    _provider_cache[key] = provider
    return provider


_retriever_cache: dict[tuple, Any] = {}


def build_retriever(spec: dict, host: str) -> tuple[Any, int]:
    """Returns (retriever, k). k is 0 for passthrough (ignored)."""
    kind = spec["kind"]
    if kind == "passthrough":
        return PassthroughRetriever(), 0
    if kind == "embedding":
        embed_model = spec.get("embed_model", "nomic-embed-text")
        key = ("embedding", embed_model, host)
        if key not in _retriever_cache:
            _retriever_cache[key] = EmbeddingRetriever(embed_model, host=host)
        return _retriever_cache[key], int(spec.get("k", 8))
    raise ValueError(f"unknown retrieval kind: {kind}")


def expand_cells(config: dict) -> list[tuple[dict, dict, str, str]]:
    """All (model_spec, retrieval_spec, decoding, prompt) combinations.

    A model spec may carry `retrieval` / `decoding` / `prompts` keys that restrict the
    global axes for that model only (e.g. a big reference model running passthrough-only).
    `retrieval` on a model spec is a list of *kinds* matched against the global entries.
    """
    retrieval = config["retrieval"]
    decoding = config.get("decoding", ["unconstrained"])
    prompts = config.get("prompts", ["default"])
    cells: list[tuple[dict, dict, str, str]] = []
    for mspec in config["models"]:
        r = [s for s in retrieval if s["kind"] in mspec["retrieval"]] \
            if "retrieval" in mspec else retrieval
        d = mspec.get("decoding", decoding)
        p = mspec.get("prompts", prompts)
        cells.extend(product([mspec], r, d, p))
    return cells
