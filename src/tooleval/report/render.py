"""JSON results -> markdown report + HTML artifact (M4).

Recomputes all per-cell / per-tier metrics from raw.jsonl rows (post-judge), produces a
comparison table, a per-tier breakdown, and a short "headline read". The HTML artifact is
the deliverable the fine-tune/ship decision is made from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TIERS = ["single", "chain", "ambiguous", "negative"]


# ---- metric helpers (operate on raw rows) -----------------------------------

def _rate(rows: list[dict], key: str) -> float | None:
    vals = [r["grade"].get(key) for r in rows]
    vals = [v for v in vals if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def _recall(rows: list[dict]) -> float | None:
    vals = [r.get("retrieval_recall") for r in rows]
    vals = [v for v in vals if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1))))], 2)


def _cell_metrics(rows: list[dict]) -> dict:
    lat = [r["latency_s"] for r in rows]
    return {
        "n": len(rows),
        "task_pass_rate": _rate(rows, "passed"),
        "tool_selection_accuracy": _rate(rows, "selection_ok"),
        "arg_validity_rate": _rate(rows, "arg_valid"),
        "arg_correctness_rate": _rate(rows, "arg_correct"),
        "chain_completion_rate": _rate([r for r in rows if r["tier"] == "chain"], "chain_complete"),
        "overcall_rate": _rate([r for r in rows if r["tier"] == "negative"], "overcalled"),
        "clarify_rate": _rate([r for r in rows if r["tier"] == "ambiguous"], "passed"),
        "retrieval_recall": _recall([r for r in rows if r["tier"] in ("single", "chain")]),
        "latency_s_p50": _pct(lat, 50),
        "latency_s_p95": _pct(lat, 95),
        "completion_tokens_mean": round(sum(r["completion_tokens"] for r in rows) / len(rows), 1)
        if rows else None,
        "errors": sum(1 for r in rows if r.get("error")),
        "turn_limit_hits": sum(1 for r in rows if r.get("hit_turn_limit")),
    }


def _cell_label(cell: dict) -> str:
    return f"{cell['model'].replace('ollama:', '')} | {cell['retrieval']} | {cell['decoding']}"


def compute(rows: list[dict]) -> list[dict]:
    """Group rows by cell, compute overall + per-tier metrics. Preserves cell order seen."""
    order: list[str] = []
    by_cell: dict[str, list[dict]] = {}
    cell_meta: dict[str, dict] = {}
    for r in rows:
        label = _cell_label(r["cell"])
        if label not in by_cell:
            by_cell[label] = []
            order.append(label)
            cell_meta[label] = r["cell"]
        by_cell[label].append(r)
    out = []
    for label in order:
        crows = by_cell[label]
        per_tier = {t: _cell_metrics([r for r in crows if r["tier"] == t])
                    for t in TIERS if any(r["tier"] == t for r in crows)}
        out.append({"label": label, "cell": cell_meta[label],
                    "overall": _cell_metrics(crows), "per_tier": per_tier})
    return out


def headline(cells: list[dict]) -> dict:
    def val(c, k):
        v = c["overall"].get(k)
        return v if v is not None else -1
    best = max(cells, key=lambda c: val(c, "task_pass_rate"))
    worst_oc = max(
        cells,
        key=lambda c: (c["overall"].get("overcall_rate") or 0.0),
    )
    return {
        "best_cell": best["label"],
        "best_pass": best["overall"]["task_pass_rate"],
        "worst_overcall_cell": worst_oc["label"],
        "worst_overcall": worst_oc["overall"].get("overcall_rate"),
    }


# ---- markdown ---------------------------------------------------------------

def _fmt(x: Any) -> str:
    return "—" if x is None else f"{x}"


def render_markdown(cells: list[dict], hl: dict, meta: dict) -> str:
    L = ["# tooleval — scorecard", "", f"_{meta.get('subtitle', '')}_", ""]
    L += ["## Cells (overall)", "",
          "| cell | pass | tool_sel | arg_ok | overcall | clarify | chain | recall | p50s |",
          "|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        o = c["overall"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            c["label"], _fmt(o["task_pass_rate"]), _fmt(o["tool_selection_accuracy"]),
            _fmt(o["arg_correctness_rate"]), _fmt(o["overcall_rate"]), _fmt(o["clarify_rate"]),
            _fmt(o["chain_completion_rate"]), _fmt(o["retrieval_recall"]), _fmt(o["latency_s_p50"])))
    L += ["", "## Headline read", "",
          f"- **Best cell:** `{hl['best_cell']}` (task_pass={hl['best_pass']})",
          f"- **Biggest over-call offender:** `{hl['worst_overcall_cell']}` "
          f"(overcall={hl['worst_overcall']})", ""]
    L += ["## Per-tier task_pass_rate", "",
          "| cell | " + " | ".join(TIERS) + " |", "|---|" + "---|" * len(TIERS)]
    for c in cells:
        cells_pt = [_fmt(c["per_tier"].get(t, {}).get("task_pass_rate")) for t in TIERS]
        L.append(f"| {c['label']} | " + " | ".join(cells_pt) + " |")
    return "\n".join(L) + "\n"


# ---- HTML artifact ----------------------------------------------------------

def render_html(cells: list[dict], hl: dict, meta: dict, recommendations: str) -> str:
    rows_html = ""
    for c in cells:
        o = c["overall"]
        oc = o.get("overcall_rate")
        oc_cls = "bad" if (oc or 0) >= 0.3 else ("warn" if (oc or 0) > 0 else "good")
        pass_cls = "good" if (o["task_pass_rate"] or 0) >= 0.85 else (
            "warn" if (o["task_pass_rate"] or 0) >= 0.6 else "bad")
        rows_html += (
            f"<tr><td class='lbl'>{c['label']}</td>"
            f"<td class='{pass_cls}'>{_fmt(o['task_pass_rate'])}</td>"
            f"<td>{_fmt(o['tool_selection_accuracy'])}</td>"
            f"<td>{_fmt(o['arg_correctness_rate'])}</td>"
            f"<td class='{oc_cls}'>{_fmt(oc)}</td>"
            f"<td>{_fmt(o['clarify_rate'])}</td>"
            f"<td>{_fmt(o['chain_completion_rate'])}</td>"
            f"<td>{_fmt(o['retrieval_recall'])}</td>"
            f"<td>{_fmt(o['latency_s_p50'])}</td></tr>"
        )

    tier_head = "".join(f"<th>{t}</th>" for t in TIERS)
    tier_rows = ""
    for c in cells:
        tds = ""
        for t in TIERS:
            v = c["per_tier"].get(t, {}).get("task_pass_rate")
            tds += f"<td>{_fmt(v)}</td>"
        tier_rows += f"<tr><td class='lbl'>{c['label']}</td>{tds}</tr>"

    return _HTML_TEMPLATE.format(
        subtitle=meta.get("subtitle", ""),
        generated=meta.get("generated", ""),
        rows=rows_html,
        tier_head=tier_head,
        tier_rows=tier_rows,
        best_cell=hl["best_cell"], best_pass=hl["best_pass"],
        worst_cell=hl["worst_overcall_cell"], worst_oc=hl["worst_overcall"],
        recommendations=recommendations,
    )


def build_report(
    results_dir: str | Path,
    meta: dict,
    recommendations: str = "",
    out_html: str | Path | None = None,
) -> tuple[Path, Path]:
    results_dir = Path(results_dir)
    rows = [json.loads(line) for line in (results_dir / "raw.jsonl").read_text().splitlines()]
    cells = compute(rows)
    hl = headline(cells)
    md = render_markdown(cells, hl, meta)
    html = render_html(cells, hl, meta, recommendations)
    md_fp = results_dir / "report.md"
    md_fp.write_text(md)
    html_fp = Path(out_html) if out_html else results_dir / "report.html"
    html_fp.parent.mkdir(parents=True, exist_ok=True)
    html_fp.write_text(html)
    return md_fp, html_fp


_HTML_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>tooleval — scorecard</title>
<style>
:root{{--bg:#fafafa;--panel:#fff;--fg:#171717;--muted:rgba(23,23,23,.62);--border:rgba(15,20,30,.1);
--c1:#4285f4;--c2:#9b72cb;--c3:#d96570;--c4:#fbbc04;--c5:#34a853;
--gradient:linear-gradient(135deg,var(--c1),var(--c2),var(--c3),var(--c4),var(--c5));
--ok:#10b981;--warn:#f59e0b;--err:#ef4444;--font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;}}
.dark{{--bg:#0a0a0a;--panel:#181c24;--fg:#ededed;--muted:rgba(237,237,237,.62);--border:rgba(255,255,255,.12);}}
*{{box-sizing:border-box;}}body{{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font);
font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased;}}
.wrap{{max-width:1080px;margin:0 auto;padding:2rem 1.5rem;}}
h1{{font-size:2rem;letter-spacing:-.02em;margin:0 0 .2em;}}h2{{font-size:1.3rem;margin:2rem 0 .6em;}}
.grad{{background:var(--gradient);background-size:200% 200%;-webkit-background-clip:text;background-clip:text;color:transparent;}}
.sub{{color:var(--muted);margin:0 0 .3em;}}.gen{{color:var(--muted);font-size:.8rem;}}
.card{{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:1.2rem 1.4rem;margin:1rem 0;box-shadow:0 1px 2px rgba(15,20,30,.05);}}
table{{width:100%;border-collapse:collapse;font-size:.86rem;}}
th,td{{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--border);}}
th{{font-weight:600;background:rgba(0,0,0,.03);font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);}}
.dark th{{background:rgba(255,255,255,.05);}}
td.lbl{{font-family:var(--mono);font-size:.78rem;white-space:nowrap;}}
td.good{{color:var(--ok);font-weight:700;}}td.warn{{color:var(--warn);font-weight:700;}}td.bad{{color:var(--err);font-weight:700;}}
.headline{{border-left:4px solid;border-image:var(--gradient) 1;padding:.8rem 1.2rem;background:rgba(66,133,244,.05);border-radius:0 12px 12px 0;}}
.toggle{{position:fixed;top:1rem;right:1rem;border:1px solid var(--border);background:var(--panel);color:var(--fg);border-radius:8px;padding:.4rem .7rem;cursor:pointer;font:inherit;}}
code{{font-family:var(--mono);font-size:.85em;background:rgba(0,0,0,.06);padding:.1em .35em;border-radius:4px;}}
.dark code{{background:rgba(255,255,255,.1);}}
.recs{{white-space:pre-wrap;font-size:.92rem;}}
ul{{margin:.3em 0;padding-left:1.2rem;}}li{{margin:.25em 0;color:var(--muted);}}
</style></head><body>
<button class="toggle" onclick="document.body.classList.toggle('dark')">◐ theme</button>
<div class="wrap">
<div class="sub">mac-agent · local tool-calling eval</div>
<h1><span class="grad">tooleval</span> scorecard</h1>
<p class="sub">{subtitle}</p><p class="gen">{generated}</p>

<div class="card headline">
<strong>Headline read</strong>
<ul>
<li><b>Best cell:</b> <code>{best_cell}</code> — task_pass = <b>{best_pass}</b></li>
<li><b>Biggest over-call offender:</b> <code>{worst_cell}</code> — overcall = <b>{worst_oc}</b> (lower is better; negative tier)</li>
</ul>
</div>

<h2>Cells — overall</h2>
<div class="card"><table>
<thead><tr><th>cell (model | retrieval | decoding)</th><th>pass</th><th>tool_sel</th><th>arg_ok</th><th>overcall</th><th>clarify</th><th>chain</th><th>recall</th><th>p50 s</th></tr></thead>
<tbody>{rows}</tbody>
</table></div>

<h2>Per-tier task pass rate</h2>
<div class="card"><table>
<thead><tr><th>cell</th>{tier_head}</tr></thead>
<tbody>{tier_rows}</tbody>
</table></div>

<h2>Recommendations &amp; read</h2>
<div class="card recs">{recommendations}</div>

</div></body></html>
"""
