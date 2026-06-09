"""Load the canonical tool catalog (ground truth) from data/tools/catalog.json."""

from __future__ import annotations

import json
from pathlib import Path

from ..types import ToolSchema

DEFAULT_CATALOG = Path("data/tools/catalog.json")


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> list[ToolSchema]:
    """Parse the catalog JSON (as emitted by catalog_builder.html) into ToolSchemas."""
    path = Path(path)
    data = json.loads(path.read_text())
    tools = data["tools"] if isinstance(data, dict) else data
    out: list[ToolSchema] = []
    for entry in tools:
        fn = entry["function"]
        out.append(
            ToolSchema(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters", {"type": "object", "properties": {}}),
                meta=entry.get("meta", {}),
            )
        )
    _assert_unique(out)
    return out


def _assert_unique(tools: list[ToolSchema]) -> None:
    seen: set[str] = set()
    for t in tools:
        if t.name in seen:
            raise ValueError(f"Duplicate tool name in catalog: {t.name}")
        seen.add(t.name)


class Catalog:
    """Indexed view over a tool list, for name lookup and subsetting."""

    def __init__(self, tools: list[ToolSchema]):
        self.tools = tools
        self._by_name = {t.name: t for t in tools}

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG) -> Catalog:
        return cls(load_catalog(path))

    def __len__(self) -> int:
        return len(self.tools)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> ToolSchema | None:
        return self._by_name.get(name)

    def subset(self, names: list[str]) -> list[ToolSchema]:
        return [self._by_name[n] for n in names if n in self._by_name]
