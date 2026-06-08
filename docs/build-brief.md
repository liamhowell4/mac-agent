# Local Tool-Calling Eval Harness — Build Brief

> Hand this file to Claude Code as the project spec. Build it incrementally per the
> **Milestones** section. Stop and confirm with me at the end of each milestone.

## 1. Purpose (read this first)

Before deciding whether to fine-tune a small local model on macOS/MCP tool use, I want a
**zero-shot eval harness** that measures how well off-the-shelf models (Qwen 3.5 4B,
Gemma 4 E4B) already handle my tool-calling tasks — *with proper scaffolding*, not naively.

This harness serves one decision: **fine-tune, or ship zero-shot?**

It must do double duty:

- **Now:** a zero-shot scorecard across models and scaffold configs.
- **Later:** the held-out test set + grader for a fine-tune, if I end up needing one.

So the task format and grader must be reusable by a future training pipeline. Don’t couple
them to the runner.

## 2. The core insight that drives the design

Zero-shot tool-calling quality is dominated by **scaffolding, not the raw model**. A fair
test is NOT “dump all tools at the model and see.” Small models degrade hard past ~10–15
tools in context. So the harness must let me independently toggle and measure:

1. **Tool retrieval** — surface only the top-k relevant tools per query (none / static-k / embedding retrieval).
1. **Constrained decoding** — force schema-valid JSON tool calls (on / off).
1. **Model** — Qwen 3.5 4B vs Gemma 4 E4B vs others (pluggable).
1. **Prompt** — system-prompt variants.

The whole point is to separate “the model can’t do this” from “my scaffold was bad.” Every
run is a `(model × retrieval × decoding × prompt)` cell, and the report compares cells.

## 3. Goals / Non-goals

**Goals**

- Score zero-shot tool-calling across a held-out task set, broken down by difficulty tier.
- Make scaffolding a first-class, toggleable variable.
- Deterministic, reproducible, side-effect-free grading.
- Reusable task format + grader for a later fine-tune.

**Non-goals (out of scope — do NOT build these)**

- Fine-tuning / synthetic-data generation (separate project; this feeds it).
- Speculative decoding / the 2B draft model (later latency optimization).
- A UI. CLI + generated report only.
- Actually performing side effects (sending mail, deleting files). See §6 simulator.

## 4. Environment & constraints

- Dev machine: M2 MacBook Air, 16GB unified memory. Eval runs here.
- Inference runtimes already installed: **Ollama**, **MLX** (`mlx_lm`). Prefer these.
- Models (Ollama tags): `qwen3.5:4b`, `gemma4:e4b`. Add more later.
- **Gemma 4 tool-call parsing in Ollama requires >= 0.20.2.** The harness must assert the
  Ollama version on startup and fail loud if older, OR allow pointing at a llama.cpp / vLLM
  OpenAI-compatible endpoint instead — otherwise we benchmark an integration bug, not the model.
- Python 3.11+, `uv` for env/deps. Async where it helps throughput.

## 5. Repo layout

```
tool-eval/
  pyproject.toml            # uv-managed
  config/
    run.example.yaml        # the run matrix + scaffold knobs
  src/tooleval/
    __init__.py
    providers/              # model adapters (pluggable)
      base.py               # Provider protocol
      ollama.py
      mlx.py
    retrieval/              # tool selection (pluggable)
      base.py               # Retriever protocol
      passthrough.py        # all tools (the unfair baseline)
      embedding.py          # top-k by embedding similarity
    tools/
      catalog.py            # load canonical tool schemas (ground truth)
      simulator.py          # deterministic, state-backed mock backend
      adapters.py           # canonical <-> model-native tool format
    eval/
      task.py               # Task + Expectation dataclasses, JSON (de)serialization
      grader.py             # programmatic + LLM-judge graders
      metrics.py            # metric definitions & aggregation
      runner.py             # orchestrates model × scaffold × task matrix
    report/
      render.py             # JSON results -> markdown report
  data/
    tools/                  # canonical tool schemas (JSON)
    tasks/                  # the eval task set (JSON, one file per tier or one bundle)
  results/                  # run outputs (gitignored)
  tests/
```

## 6. Core abstractions (define these as protocols/dataclasses first)

**Canonical tool schema** = OpenAI function-calling JSON Schema. This is the single source
of truth. `tools/adapters.py` converts canonical → model-native (Qwen chatml tool format,
Gemma 4 native special-token format) and parses model output back into a normalized
`ToolCall(name, arguments: dict)`.

**Provider** (`providers/base.py`)

```python
class Provider(Protocol):
    name: str
    def complete(self, messages: list[Msg], tools: list[ToolSchema],
                 constrained: bool) -> Completion: ...
    # Completion carries: tool_calls: list[ToolCall], text: str|None,
    # latency_s: float, prompt_tokens: int, completion_tokens: int
```

- `ollama.py`: use the native `/api/chat` tool path (better tool parsing than the
  OpenAI-compat `/v1` path for these models). `constrained=True` → pass JSON-schema
  structured output / `format`.
- `mlx.py`: `mlx_lm` generate; constrained via grammar if available, else best-effort.

**Retriever** (`retrieval/base.py`)

```python
class Retriever(Protocol):
    def select(self, query: str, catalog: list[ToolSchema], k: int) -> list[ToolSchema]: ...
```

- `passthrough`: returns all (the deliberately-unfair baseline, to quantify retrieval’s value).
- `embedding`: embed tool name+description (local embed model, e.g. `nomic-embed-text` via
  Ollama), cosine top-k. Default `k=8`, hard cap 15.

**Tool simulator** (`tools/simulator.py`) — THE important piece.

- Deterministic, state-backed mock of the tool backend so multi-turn chains stay consistent
  (“am I free at 3?” must agree with the seeded calendar) and nothing real happens.
- Seeded fake state: calendar, inbox, contacts, files, system settings.
- Given a `ToolCall`, return a plausible result string/JSON. Fixed seed → reproducible.
- We grade on the **call** (name + args), not on real side effects.

**Task / Expectation** (`eval/task.py`) — see §7.

**Grader** (`eval/grader.py`) — see §8.

## 7. Eval dataset spec

50–150 tasks. Four tiers, roughly balanced. **Include some tasks whose correct tools are
held out of any future fine-tune split**, so we can later measure generalization, not a
home-field number.

Tiers:

- `single` — one tool, one call.
- `chain` — ordered multi-tool (the thing small models fail on).
- `ambiguous` — underspecified; correct behavior is to ask a clarifying question, NOT call.
- `negative` — no tool applies; correct behavior is a direct answer / refusal, NOT a call.
  (This tier measures **over-calling**, the #1 small-model failure.)

Task JSON schema:

```json
{
  "id": "cal_001",
  "tier": "single",
  "messages": [{"role": "user", "content": "move my 2pm to 3:30"}],
  "catalog": ["calendar.list_events", "calendar.update_event", "..."],
  "expect": {
    "kind": "tool_call",                // tool_call | no_call | clarify | chain
    "calls": [
      {"name": "calendar.update_event",
       "args": {"event_id": "*", "new_start": "15:30"},   // "*" = any/unchecked
       "arg_match": {"new_start": "exact", "event_id": "present"}}
    ]
  },
  "notes": "needs to resolve '2pm' against seeded calendar via list_events first? mark as chain if so"
}
```

- `arg_match` per-field: `exact` | `normalized` (case/format-insensitive) | `present`
  (key exists, value unchecked) | `semantic` (defer to judge).
- For `chain`, `calls` is ordered; grader checks order + each call against simulator state.
- For `clarify`/`no_call`, `calls` is empty and the judge checks the text response.

Ship ~15–20 seed tasks per tier authored by hand so the harness is runnable immediately;
I’ll expand the set after.

## 8. Grading strategy

Programmatic first, judge only where needed:

- **Tool selection**: predicted tool name ∈ expected set. Programmatic.
- **Argument validity**: (a) schema-valid against canonical schema; (b) values match per
  `arg_match` rules. Programmatic.
- **Chain**: ordered subsequence match + each step valid against simulator state. Programmatic.
- **no_call / clarify**: did the model abstain from calling? Programmatic for the abstain
  check; an **LLM judge** rates whether the clarifying question / direct answer is appropriate.
- Judge calls must be flagged in output and use a separate configurable model
  (default: a stronger local model or a cloud model — make it swappable). Keep judge usage minimal.

## 9. Metrics (`eval/metrics.py`)

Per cell `(model × retrieval × decoding × prompt)`, aggregated overall and **per tier**:

- `tool_selection_accuracy`
- `arg_validity_rate` (schema-valid) and `arg_correctness_rate` (matches expected)
- `chain_completion_rate` (chains only)
- `overcall_rate` = false tool calls on `negative` tier (lower better) — **headline metric**
- `clarify_rate` = correct abstain+ask on `ambiguous` (higher better)
- `task_pass_rate` (all-criteria-pass per task)
- `latency_s` p50/p95, `completion_tokens` mean

## 10. Config (`config/run.example.yaml`)

```yaml
models:
  - {provider: ollama, model: "qwen3.5:4b"}
  - {provider: ollama, model: "gemma4:e4b"}
retrieval:
  - {kind: passthrough}
  - {kind: embedding, k: 8}
decoding: [constrained, unconstrained]
prompts: ["default"]            # add variants later
judge: {provider: ollama, model: "qwen3.5:9b"}
tasks: "data/tasks/*.json"
seed: 42
```

The runner expands this into the full matrix of cells and runs every task in each.

## 11. Output / reporting

- `results/<timestamp>/raw.jsonl` — one row per (cell, task) with full trace
  (messages sent, tools offered, raw model output, parsed calls, grades).
- `results/<timestamp>/report.md` — a table comparing cells on the §9 metrics, with a
  per-tier breakdown and a short “headline read” (best cell, biggest over-call offender).
- Make traces easy to eyeball; I’ll be reading failures by hand.

## 12. Milestones (build in this order; pause for confirmation after each)

**M1 — Skeleton + single-tier MVP.** Repo, `pyproject`, canonical schema loader, Ollama
provider, `passthrough` retriever, simulator with seeded state, `single` + `negative` tiers,
programmatic grader, JSON output. Acceptance: `tooleval run` scores `qwen3.5:4b` on the seed
tasks and prints tool_selection_accuracy + overcall_rate.

**M2 — Full matrix.** Add embedding retriever, constrained-decoding toggle, Gemma 4 provider
path (+ Ollama version assert), config-driven matrix runner. Acceptance: one config runs all
cells and emits per-cell metrics.

**M3 — Chains + ambiguous + judge.** Add `chain` and `ambiguous` tiers, ordered-chain grading
against simulator state, LLM-judge for clarify/no_call. Acceptance: all four tiers scored.

**M4 — Report + polish.** Markdown report with per-tier comparison and headline read; MLX
provider; tests for adapters/grader. Acceptance: `report.md` I can read to make the
fine-tune/ship call.

## 13. Decisions I still owe you (ask me if blocking)

- Which MCP servers’ real schemas to seed the catalog from (`apple-mcp` vs `macuse`); for now,
  hand-author ~15–25 representative tool schemas in `data/tools/` so M1 isn’t blocked.
- Whether the judge should be local-only or allowed a cloud call.

## 14. Conventions

- Type hints + `ruff` + `pytest`. Small, pure functions for graders (they get reused downstream).
- No network calls except to the configured inference endpoints.
- Everything seedable and reproducible.

---

## Addendum — decisions resolved during planning (2026-06-08)

These resolve open questions in §13 and refine §6–§9. See `CLAUDE.md` for the canonical list.

- **Execution model:** full agentic loop (model ↔ simulator, turn cap 6).
- **Constrained decoding:** constrains arguments only; the model can always abstain.
- **Judge:** local-only, Qwen 3.5 / Gemma 4 families only, ceiling Gemma 4 12B-q4 used sparingly;
  default routine judge Qwen 3.5 9B. Resolves the §13 local-vs-cloud question (local-only).
- **Memory:** batch all generation + programmatic grading first, then a separate resumable judge
  pass — never co-reside a 4B model and the 12B-q4 judge on 16GB.
- **Extra metric:** `retrieval_recall@k` (gold tool survived top-k) for failure attribution.
- **Catalog:** hand-authored via `catalog_builder.html`; includes deliberate distractors; domains
  cover calendar, mail, messages, contacts, files, system (+ music/web from the product surface).
- **Negative tier** is pure no-call; `web_search` cases (if in scope) are separate, not negatives.
- **Report** flags same-family judge/model overlap with a skepticism caveat.
