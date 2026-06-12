"""Live TUI tracker for a running `tooleval matrix` — run it in a second terminal/tmux pane.

Usage:
  uv run python scripts/watch_matrix.py                 # newest results dir + newest *.log
  uv run python scripts/watch_matrix.py results/<ts>    # watch a specific run

Reads raw.jsonl (one row appended per completed task) and the live log; nothing here
touches the run itself. Ctrl-C to quit the watcher — the run is unaffected.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

RESULTS = Path("results")


def newest_run_dir() -> Path | None:
    runs = sorted((d for d in RESULTS.iterdir() if d.is_dir() and d.name[0].isdigit()),
                  key=lambda d: d.name)
    return runs[-1] if runs else None


def newest_log() -> Path | None:
    logs = sorted(RESULTS.glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def read_meta(log: Path | None) -> tuple[int | None, int | None, bool, list[str]]:
    """(total_cells, tasks_per_cell, finished, announced cell labels) from the live log."""
    if not log or not log.exists():
        return None, None, False, []
    head = log.read_text(errors="replace")
    m = re.search(r"tasks=(\d+)\s+cells=(\d+)", head)
    cells = int(m.group(2)) if m else None
    tasks = int(m.group(1)) if m else None
    announced = []
    for line in re.findall(r"\[cell \d+/\d+\] (.+)", head):
        model, retr, _dec, prompt = (p.strip() for p in line.split(" | "))
        announced.append(f"{model.replace('ollama:', '')} · {retr.split(':')[0]} · {prompt}")
    return cells, tasks, "MATRIX_EXIT_" in head, announced


def load_rows(run_dir: Path) -> list[dict]:
    fp = run_dir / "raw.jsonl"
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a row mid-write; it'll parse next refresh
    return rows


def cell_label(cell: dict) -> str:
    retr = cell["retrieval"].split(":")[0]  # embedding:<model> → embedding
    return f"{cell['model'].replace('ollama:', '')} · {retr} · {cell.get('prompt', 'default')}"


def render(run_dir: Path, log: Path | None, t0: float, baseline: int) -> Group:
    rows = load_rows(run_dir)
    n_cells, per_cell, finished, announced = read_meta(log)
    per_cell = per_cell or 66

    by_cell: dict[str, list[dict]] = {label: [] for label in announced}
    for r in rows:
        by_cell.setdefault(cell_label(r["cell"]), []).append(r)

    table = Table(box=None, pad_edge=False)
    table.add_column("cell", min_width=38)
    table.add_column("progress", min_width=22)
    table.add_column("pass", justify="right")
    table.add_column("overcall", justify="right")
    table.add_column("err", justify="right")

    for label, cell_rows in by_cell.items():
        done = len(cell_rows)
        passed = sum(r["grade"]["passed"] for r in cell_rows)
        negs = [r for r in cell_rows if r["tier"] == "negative"]
        over = sum(bool(r["grade"].get("overcalled")) for r in negs)
        errs = sum(bool(r.get("error")) for r in cell_rows)
        bar = ProgressBar(total=per_cell, completed=done, width=16)
        live_marker = "" if done >= per_cell else " ◂"
        table.add_row(
            Text(label + live_marker, style="bold" if done < per_cell else "dim"),
            Group(bar, Text(f" {done}/{per_cell}", style="dim")),
            f"{passed / done:.2f}" if done else "—",
            f"{over}/{len(negs)}" if negs else "—",
            str(errs) if errs else "·",
        )

    total_done = len(rows)
    total = (n_cells or max(len(by_cell), 1)) * per_cell
    # rate from rows completed since the watcher started, so pre-existing rows don't skew it
    uncached = [r for r in rows if not r.get("cached")]
    rate = max(len(uncached) - baseline, 0) / max(time.time() - t0, 1)
    remaining = total - total_done
    eta = f"{remaining / rate / 60:.0f} min" if rate > 0 and remaining > 0 else "—"

    if finished:
        status = Text("RUN COMPLETE", style="bold green")
    else:
        status = Text(
            f"{total_done}/{total} tasks · {rate:.2f} tasks/s · ETA ~{eta}",
            style="bold cyan",
        )
    header = Panel(status, title=f"tooleval · {run_dir.name}", title_align="left")
    return Group(header, table)


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    t0 = time.time()
    baseline = 0
    watched: Path | None = None
    with Live(refresh_per_second=1, screen=False) as live:
        while True:
            rd = run_dir or newest_run_dir()
            log = newest_log()
            if rd is None:
                live.update(Text("waiting for a results dir...", style="dim"))
            else:
                if rd != watched:  # new run appeared (or first sight) — reset rate baseline
                    watched, t0 = rd, time.time()
                    baseline = sum(not r.get("cached") for r in load_rows(rd))
                live.update(render(rd, log, t0, baseline))
            time.sleep(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
