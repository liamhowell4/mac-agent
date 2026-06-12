"""Shared session factory for the CLI and daemon.

Environment overrides (used by the app-spawned daemon, which has no CLI flags):
  QUAKE_MODEL  — Ollama model tag (default: the eval-validated ship model)
  QUAKE_HOST   — Ollama host (default http://localhost:11434)
  QUAKE_SIM    — "1" runs against the simulator (no real actions)
  QUAKE_THINK  — "on" re-enables the reasoning channel (default off: the dev eval
                 measured identical accuracy at ~half the latency with think off)
"""

from __future__ import annotations

import os

from tooleval.providers.ollama import OllamaProvider
from tooleval.tools.catalog import load_catalog

from .agent import AgentSession

SHIP_MODEL = "hf.co/unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q4_K_XL"


def build_session(
    model: str | None = None,
    host: str | None = None,
    sim: bool | None = None,
    think: bool | None = None,
) -> AgentSession:
    model = model or os.environ.get("QUAKE_MODEL", SHIP_MODEL)
    host = host or os.environ.get("QUAKE_HOST", "http://localhost:11434")
    if sim is None:
        sim = os.environ.get("QUAKE_SIM", "") == "1"
    if think is None:
        think = os.environ.get("QUAKE_THINK", "off") == "on"

    catalog = load_catalog()
    provider = OllamaProvider(model, host=host, think=think)
    if sim:
        from tooleval.tools.simulator import Simulator  # noqa: PLC0415

        executor = Simulator(catalog)
    else:
        from .executor import Executor  # noqa: PLC0415

        executor = Executor(catalog)
    return AgentSession(provider, catalog, executor)
