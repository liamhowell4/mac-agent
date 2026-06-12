"""Aggregates per-domain HANDLERS into one dispatch table.

Modules are discovered dynamically so partially-built domains don't break imports;
tests/test_quake_executors.py asserts full catalog coverage once all domains land.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable

ALL_HANDLERS: dict[str, Callable[[dict], dict]] = {}

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name.startswith("_"):
        continue
    module = importlib.import_module(f"{__name__}.{_mod.name}")
    handlers = getattr(module, "HANDLERS", {})
    overlap = set(handlers) & set(ALL_HANDLERS)
    if overlap:
        raise RuntimeError(f"duplicate tool handlers: {sorted(overlap)}")
    ALL_HANDLERS.update(handlers)


def missing_tools(catalog) -> list[str]:
    """Catalog tools without a handler (assistant.ask_user is loop-level, never executed)."""
    return sorted(
        t.name for t in catalog
        if t.name not in ALL_HANDLERS and t.name != "assistant.ask_user"
    )
