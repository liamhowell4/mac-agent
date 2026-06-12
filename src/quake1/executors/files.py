"""files.* — pure Python where possible; Trash via Finder so it's recoverable."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from ._util import ToolError, as_quote, run_osascript

SEARCH_ROOTS = ("~/Documents", "~/Downloads", "~/Desktop", "~/Pictures")
MAX_RESULTS = 50


def _expand(p: str) -> Path:
    if not p:
        raise ToolError("a path is required")
    return Path(p).expanduser()


def _resolve_existing(p: str) -> Path:
    path = _expand(p)
    if not path.exists():
        raise ToolError(f"No such file or folder: {path}")
    return path


def search(args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("a search query is required")
    root = args.get("path")
    roots = [_expand(root)] if root else [Path(r).expanduser() for r in SEARCH_ROOTS]
    # Spotlight first (fast, indexed); fall back to a bounded glob walk
    hits: list[str] = []
    try:
        scoped = [a for r in roots for a in ("-onlyin", str(r))]
        out = subprocess.run(
            ["mdfind", *scoped, query], capture_output=True, text=True, timeout=10
        ).stdout
        hits = [h for h in out.splitlines() if h.strip()][:MAX_RESULTS]
    except Exception:  # noqa: BLE001 — fall through to the walk
        hits = []
    if not hits:
        tokens = [t.lower() for t in query.split()]
        for r in roots:
            if not r.is_dir():
                continue
            for p in r.rglob("*"):
                if len(hits) >= MAX_RESULTS:
                    break
                if p.is_file() and any(t in p.name.lower() for t in tokens):
                    hits.append(str(p))
    return {"files": hits}


def list_dir(args: dict) -> dict:
    p = _resolve_existing(args.get("path") or "~")
    if not p.is_dir():
        raise ToolError(f"Not a directory: {p}")
    entries = sorted(p.iterdir(), key=lambda e: e.name.lower())[:100]
    return {"path": str(p), "files": [
        {"name": e.name, "kind": "dir" if e.is_dir() else "file"} for e in entries]}


def read(args: dict) -> dict:
    p = _resolve_existing(args["path"])
    if p.stat().st_size > 200_000:
        raise ToolError(f"{p.name} is too large to read inline ({p.stat().st_size} bytes)")
    try:
        return {"path": str(p), "content": p.read_text(errors="replace")[:8000]}
    except UnicodeDecodeError as e:
        raise ToolError(f"{p.name} is not a text file") from e


def get_info(args: dict) -> dict:
    p = _resolve_existing(args["path"])
    st = p.stat()
    return {"path": str(p), "size_bytes": st.st_size, "modified": int(st.st_mtime),
            "kind": "dir" if p.is_dir() else "file"}


def recent(args: dict) -> dict:
    limit = int(args.get("limit") or 10)
    candidates: list[Path] = []
    for r in (Path(x).expanduser() for x in SEARCH_ROOTS):
        if r.is_dir():
            candidates += [p for p in r.iterdir() if p.is_file()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"files": [str(p) for p in candidates[:limit]]}


def storage_info(args: dict) -> dict:
    usage = shutil.disk_usage(str(_expand(args.get("path") or "~")))
    return {"total_gb": round(usage.total / 1e9, 1), "free_gb": round(usage.free / 1e9, 1)}


def create_folder(args: dict) -> dict:
    p = _expand(args["path"])
    p.mkdir(parents=True, exist_ok=True)
    return {"created": str(p)}


def copy(args: dict) -> dict:
    src = _resolve_existing(args["src"])
    dest = _expand(args["dest"])
    if dest.is_dir():
        dest = dest / src.name
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return {"copied": str(src), "to": str(dest)}


def move(args: dict) -> dict:
    src = _resolve_existing(args["src"])
    dest = _expand(args["dest"])
    if dest.is_dir():
        dest = dest / src.name
    shutil.move(str(src), str(dest))
    return {"moved": str(src), "to": str(dest)}


def rename(args: dict) -> dict:
    p = _resolve_existing(args["path"])
    new = p.with_name(str(args["new_name"]))
    p.rename(new)
    return {"renamed": str(p), "to": str(new)}


def open_(args: dict) -> dict:
    p = _resolve_existing(args["path"])
    subprocess.run(["open", str(p)], check=True, timeout=10)
    return {"opened": str(p)}


def compress(args: dict) -> dict:
    paths = args.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise ToolError("paths to compress are required")
    resolved = [_resolve_existing(p) for p in paths]
    dest = _expand(args.get("dest") or f"~/Desktop/archive_{int(time.time())}.zip")
    argv = ["ditto", "-c", "-k", "--sequesterRsrc"]
    if len(resolved) == 1:
        subprocess.run([*argv, str(resolved[0]), str(dest)], check=True, timeout=120)
    else:
        # zip multiple items via a Finder-style parent capture: fall back to zip CLI
        subprocess.run(["zip", "-r", str(dest), *[str(p) for p in resolved]],
                       check=True, timeout=120)
    return {"archive": str(dest)}


def trash(args: dict) -> dict:
    p = _resolve_existing(args["path"])
    run_osascript(f'tell application "Finder" to delete POSIX file {as_quote(str(p))}')
    return {"trashed": str(p)}


def delete(args: dict) -> dict:
    # dangerous-flagged; still go to Trash rather than rm — recoverable beats gone
    return {"deleted_to_trash": trash(args)["trashed"]}


HANDLERS = {
    "files.search": search,
    "files.list_dir": list_dir,
    "files.read": read,
    "files.get_info": get_info,
    "files.recent": recent,
    "files.storage_info": storage_info,
    "files.create_folder": create_folder,
    "files.copy": copy,
    "files.move": move,
    "files.rename": rename,
    "files.open": open_,
    "files.compress": compress,
    "files.trash": trash,
    "files.delete": delete,
}
