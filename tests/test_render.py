"""Report renderer tests. Guards the prompt-axis regression: cells must not collapse
default/restraint runs into one averaged row."""

from tooleval.report.render import _cell_label, compute


def _row(model, retrieval, decoding, prompt, tier, passed, overcalled=None):
    grade = {"passed": passed, "selection_ok": passed,
             "arg_correct": passed, "overcalled": overcalled}
    return {
        "cell": {"model": model, "retrieval": retrieval, "decoding": decoding, "prompt": prompt},
        "tier": tier, "grade": grade, "retrieval_recall": 1.0, "latency_s": 1.0,
        "completion_tokens": 10,
    }


def test_cell_label_includes_prompt():
    base = {"model": "ollama:qwen", "retrieval": "passthrough", "decoding": "unconstrained"}
    default = _cell_label({**base, "prompt": "default"})
    restraint = _cell_label({**base, "prompt": "restraint"})
    assert default != restraint
    assert "restraint" in restraint


def test_default_and_restraint_are_separate_cells():
    # same model/retrieval/decoding, different prompt → must stay two cells, not be averaged
    rows = [
        _row("ollama:gemma", "passthrough", "unconstrained", "default", "negative", False, 1.0),
        _row("ollama:gemma", "passthrough", "unconstrained", "restraint", "negative", True, 0.0),
    ]
    cells = compute(rows)
    assert len(cells) == 2
    labels = {c["label"] for c in cells}
    assert any("default" in label for label in labels)
    assert any("restraint" in label for label in labels)


def test_missing_prompt_defaults_to_default():
    # legacy rows without a prompt key must not crash and should label as "default"
    cell = {"model": "ollama:qwen", "retrieval": "passthrough", "decoding": "unconstrained"}
    assert "default" in _cell_label(cell)
