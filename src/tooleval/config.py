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


def build_provider(spec: dict, host: str, seed: int):
    if spec["provider"] == "ollama":
        return OllamaProvider(spec["model"], host=host, seed=seed)
    raise NotImplementedError(f"provider '{spec['provider']}' not available (MLX lands in M4)")


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
    """All (model_spec, retrieval_spec, decoding, prompt) combinations."""
    models = config["models"]
    retrieval = config["retrieval"]
    decoding = config.get("decoding", ["unconstrained"])
    prompts = config.get("prompts", ["default"])
    return list(product(models, retrieval, decoding, prompts))
