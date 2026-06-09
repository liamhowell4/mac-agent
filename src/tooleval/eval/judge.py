"""LLM-judge pass (M3) — rates the *quality* of clarify-tier responses.

Separate, optional, resumable stage (per the build brief): programmatic grading already
decided whether the model abstained; the judge only rates whether an abstaining response
was an *appropriate clarifying question*. Cloud (OpenRouter), so no local memory cost.
Verdicts are cached for reproducibility (cloud isn't bit-deterministic even at temp 0).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..providers.openrouter import OpenRouterError, OpenRouterJudge

CLARIFY_SYSTEM = (
    "You grade an AI assistant that controls a Mac via tools. The user's request was "
    "AMBIGUOUS, so the correct behavior is to ask a clarifying question instead of guessing "
    "or acting. Reply with ONLY 'YES' or 'NO': did the assistant ask an appropriate, specific "
    "clarifying question that would resolve the ambiguity?"
)


def _final_text(row: dict) -> str | None:
    for turn in reversed(row.get("trace", [])):
        if turn.get("text"):
            return turn["text"]
    return None


def _parse_verdict(out: str) -> bool:
    u = out.strip().upper()
    if u.startswith("YES"):
        return True
    if u.startswith("NO"):
        return False
    return "YES" in u and "NO" not in u


def judge_rows(
    rows: list[dict],
    tasks_by_id: dict,
    judge: OpenRouterJudge,
    cache_dir: Path | None = Path("results/cache"),
) -> dict:
    """Annotate clarify rows in place with a judge verdict; return a small summary.

    For a clarify task, passed = (abstained AND judge says the question was appropriate).
    """
    judged = 0
    errors = 0
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        g = row.get("grade", {})
        if g.get("kind") != "clarify":
            continue
        if not g.get("abstained"):
            g["passed"] = False  # it acted/guessed instead of asking — fail regardless of judge
            row["judge_verdict"] = None
            continue
        final = _final_text(row) or ""
        task = tasks_by_id.get(row["task_id"], {})
        key = hashlib.sha256(
            f"{judge.model}|{row['task_id']}|{final}".encode()
        ).hexdigest()[:20]
        cache_fp = cache_dir / f"judge_{key}.json" if cache_dir else None

        verdict: bool | None
        if cache_fp and cache_fp.exists():
            verdict = json.loads(cache_fp.read_text())["verdict"]
        else:
            user = (
                f'User: "{task.get("query", "")}"\n'
                f'(Context — why it is ambiguous: {task.get("notes", "")})\n'
                f'Assistant: "{final}"'
            )
            try:
                out = judge.complete(CLARIFY_SYSTEM, user, max_tokens=2000)
                verdict = _parse_verdict(out)
                judged += 1
                if cache_fp:
                    cache_fp.write_text(json.dumps({"verdict": verdict, "raw": out}))
            except OpenRouterError:
                verdict = None
                errors += 1
        row["judge_verdict"] = verdict
        if verdict is not None:
            g["passed"] = bool(g.get("abstained") and verdict)
        # if the judge errored (verdict None), keep the programmatic abstained-based pass
    return {"judged": judged, "errors": errors}
