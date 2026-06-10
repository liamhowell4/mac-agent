"""Paired per-task comparison between cells (same 66 tasks → McNemar beats rate-diffs).

Usage:
  python scripts/compare_cells.py results/<ts>                 # all 1-axis-apart pairs
  python scripts/compare_cells.py results/<ts> --a "<label>" --b "<label>"   # one pair, with flips

For a pair of cells, the informative counts are the discordant tasks: n01 (A failed,
B passed) and n10 (A passed, B failed). The exact McNemar p-value is a two-sided
binomial test on those flips — far more powerful at n=66 than comparing aggregate rates.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from math import comb
from pathlib import Path


def _label(cell: dict) -> str:
    prompt = cell.get("prompt", "default")
    return f"{cell['model'].replace('ollama:', '')} | {cell['retrieval']} | {prompt}"


def load(results_dir: Path) -> dict[str, dict[str, dict]]:
    """label -> task_id -> row (unconstrained rows only; constrained is footnoted)."""
    by: dict[str, dict[str, dict]] = {}
    for line in (results_dir / "raw.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["cell"].get("decoding") == "constrained":
            continue
        by.setdefault(_label(r["cell"]), {})[r["task_id"]] = r
    return by


def mcnemar_p(n01: int, n10: int) -> float:
    """Exact two-sided binomial test on discordant pairs."""
    n = n01 + n10
    if n == 0:
        return 1.0
    k = min(n01, n10)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def compare(a_rows: dict[str, dict], b_rows: dict[str, dict]) -> dict:
    shared = sorted(set(a_rows) & set(b_rows))
    flips_ab = [t for t in shared
                if not a_rows[t]["grade"]["passed"] and b_rows[t]["grade"]["passed"]]
    flips_ba = [t for t in shared
                if a_rows[t]["grade"]["passed"] and not b_rows[t]["grade"]["passed"]]
    return {
        "n_shared": len(shared),
        "a_pass": sum(a_rows[t]["grade"]["passed"] for t in shared),
        "b_pass": sum(b_rows[t]["grade"]["passed"] for t in shared),
        "b_fixes": flips_ab,   # tasks B passed that A failed
        "b_breaks": flips_ba,  # tasks B failed that A passed
        "p": mcnemar_p(len(flips_ab), len(flips_ba)),
    }


def _one_axis_apart(la: str, lb: str) -> bool:
    pa, pb = la.split(" | "), lb.split(" | ")
    return len(pa) == len(pb) and sum(x != y for x, y in zip(pa, pb, strict=True)) == 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path)
    ap.add_argument("--a", default=None)
    ap.add_argument("--b", default=None)
    args = ap.parse_args()

    by = load(args.results)
    if args.a and args.b:
        if args.a not in by or args.b not in by:
            print("Known cells:\n  " + "\n  ".join(sorted(by)), file=sys.stderr)
            return 1
        c = compare(by[args.a], by[args.b])
        print(f"A: {args.a}  ({c['a_pass']}/{c['n_shared']})")
        print(f"B: {args.b}  ({c['b_pass']}/{c['n_shared']})")
        print(f"McNemar exact p = {c['p']:.4f}  "
              f"(B fixes {len(c['b_fixes'])}, B breaks {len(c['b_breaks'])})")
        for tag, ids in (("B fixes", c["b_fixes"]), ("B breaks", c["b_breaks"])):
            for t in ids:
                print(f"  [{tag}] {t}")
        return 0

    print(f"{'changed axis':<55}{'context (shared axes)':<48}{'Δpass':>7}{'fix/brk':>9}{'p':>8}")
    print("-" * 127)
    for la, lb in combinations(sorted(by), 2):
        if not _one_axis_apart(la, lb):
            continue
        c = compare(by[la], by[lb])
        if not c["n_shared"]:
            continue
        pa, pb = la.split(" | "), lb.split(" | ")
        changed = next(f"{x} → {y}" for x, y in zip(pa, pb, strict=True) if x != y)
        shared = " | ".join(x for x, y in zip(pa, pb, strict=True) if x == y)
        delta = (c["b_pass"] - c["a_pass"]) / c["n_shared"]
        sig = " *" if c["p"] < 0.05 else ""
        print(f"{changed[:54]:<55}{shared[:47]:<48}"
              + f"{delta:+.3f}{len(c['b_fixes']):>5}/{len(c['b_breaks']):<3}"
              + f"{c['p']:>7.3f}{sig}")
    print("\n* = p<0.05 (exact McNemar). Δpass/fix/brk are B−A where the changed axis reads A → B.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
