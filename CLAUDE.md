# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A **local tool-calling eval harness** (working name `tooleval`) for deciding one thing:
**fine-tune a small local model for macOS/MCP tool use, or ship zero-shot?** It measures how
well off-the-shelf small models (Qwen 3.5 4B, Gemma 4 E4B) handle tool-calling tasks *with
proper scaffolding*. The harness does double duty: a zero-shot scorecard now, and the held-out
test set + grader for a future fine-tune. The product north star is a Macatron/Mac-1-style local
assistant that controls macOS via hundreds of tools (calendar, mail, messages, files, system, etc.).

The full spec is the **build brief** at `docs/build-brief.md` — the source of truth for repo
layout (§5), abstractions (§6), dataset (§7), grading (§8), metrics (§9), config (§10), and
milestones (§12). Its addendum lists the resolved decisions; read it before building. A visual,
step-by-step roadmap lives at `.claude/artifacts/plan-tooleval.html` (open in a browser).

## Current state (important)

The harness is **not built yet**. Real artifacts so far: `catalog_builder.html`, `CLAUDE.md`,
`docs/build-brief.md`, and `.claude/artifacts/plan-tooleval.html`. Do NOT assume the
`src/tooleval/` tree, `pyproject.toml`, or any commands below exist — they are the *target*, not
the present. M1 (skeleton + single/negative MVP) is the next implementation step, pending the
user's greenlight; the user is treating this as planning-first.

The catalog now holds **103 tools across 24 domains** (calendar, mail, messages, contacts, files,
system, music, web, apps, clipboard, screen, weather, network, devices, calls, reminders, shell,
notes, + gap-only domains). It is large enough that `passthrough` genuinely breaks a 4B model —
which is the point. Dangerous ops (`shell.run_command`, `system.restart_or_shutdown`,
`files.delete`/`files.compress`) are prime material for negative/safety tasks. A few tools are
flagged `held_out` as fine-tune-generalization seeds; `notes.create` is the lone distractor.

## `catalog_builder.html`

A self-contained, dependency-free browser form for hand-authoring the canonical tool catalog.
Open it (`open catalog_builder.html`), edit tools (name, domain, description, params, and the
`read_only` / `distractor` / `held_out` flags), then **Copy JSON** and paste back. Editing the
seed lives in the `seed()` JS function; `DOMAINS`/`TYPES` arrays drive the dropdowns. Output shape
per tool: a canonical **OpenAI function-calling schema** under `function`, plus a sibling `meta`
object (`domain`, `read_only`, `distractor`, `held_out`) that the harness consumes and model
adapters ignore. A browser reload resets the form to `seed()` — copy unsaved JSON out first.

## Locked design decisions (agreed in planning — not discoverable from code)

- **Execution = full agentic loop.** The runner feeds simulator tool results back to the model and
  repeats until it emits no tool call or hits a **turn cap of 6** (cap-hit = fail, logged
  `turn_limit`). Needed because chains — and even some "single" tasks (resolve "2pm" via
  `list_events` first) — require multi-turn state.
- **Constrained decoding constrains *arguments only*, never forces a call.** The model must always
  be able to abstain, or the `negative`/`ambiguous` tiers become meaningless (guaranteed 100%
  over-call) under constrained decoding.
- **Tier is a reporting label; `expect` drives grading.** What makes a task `chain` is multiple
  *primary/mutating* calls, not the presence of a read-only lookup turn.
- **Judge = cloud via OpenRouter (revised 2026-06-08).** The judge only scores the *eval*, never
  ships in the product, so a cloud dependency doesn't touch Mac-1's local property. Default
  `nvidia/nemotron-3-ultra-550b-a55b:free` via OpenRouter's OpenAI-compatible `/chat/completions`.
  Key = `OPENROUTER_API_KEY` and model = `OPENROUTER_MODEL`, both in `.env` (gitignored). Judge stays
  **provider-swappable** (local Qwen 3.5 / Gemma 4 still selectable in run.yaml). Cloud judge is a
  *different family* from the models-under-test → removes the same-family caveat. Cache judge verdicts
  for reproducibility (cloud isn't bit-deterministic even at temp 0).
- **Memory orchestration** only matters if a *local* judge is chosen: never co-reside a 4B model +
  12B judge on 16GB. With the cloud judge default this is moot — local memory only ever holds the
  model-under-test. Either way the judge pass is a separate, optional, resumable stage, and all
  headline metrics are programmatic (scorecard usable even with judging skipped).
- **Add `retrieval_recall@k`** (did the gold tool survive top-k?) so embedding-retrieval failures
  are attributable to the retriever vs the model. Trivially 1.0 under `passthrough`.
- **`passthrough` runs the full catalog** (the deliberately-unfair baseline) — its job is to
  demonstrate small-model collapse as the catalog grows, quantifying retrieval's value.
- **Distractors are first-class:** the catalog needs plausible-but-wrong tools (e.g. `reminders.create`
  vs `calendar.create_event`) so tool-selection and over-call are meaningful.
- **`negative` tier = pure no-call** (answer directly). If a `web_search` tool is in scope, factual
  questions are *search-instead* cases, NOT negatives — keep them separate.
- **Report flags same-family judge/model overlap** (Qwen judging Qwen) with a skepticism caveat.
- **Headline metric is `overcall_rate`** on the `negative` tier — the #1 small-model failure.

## Planned conventions (per build brief — apply once implementation starts)

- Python 3.11+, **`uv`** for env/deps; **`ruff`** lint; **`pytest`**. Async where it helps throughput
  (note: local inference is serialized on one GPU, so async mostly overlaps IO, not generation).
- Type hints everywhere. Graders are **small, pure functions** — they get reused by the future
  fine-tune pipeline, so keep them decoupled from the runner.
- Everything **seedable and reproducible**. No network except the configured inference endpoints.
- Assert the **Ollama version on startup** (Gemma 4 tool parsing needs ≥ 0.20.2) and fail loud,
  or allow pointing at an OpenAI-compatible endpoint — otherwise you benchmark an integration bug.
- A **`(cell, task)` result cache** keyed by a config hash makes runs resumable; build it in from M1.
- Canonical tool schema = OpenAI function format (single source of truth); `tools/adapters.py`
  converts canonical ↔ model-native and parses output back into a normalized `ToolCall(name, args)`.
- Every run is a `(model × retrieval × decoding × prompt)` **cell**; the runner expands the config
  matrix and runs every task in each cell. Metrics aggregate overall and **per tier**.

## Milestone order (build sequentially, pause for confirmation after each)

M1 skeleton + `single`/`negative` MVP (Ollama provider, passthrough, simulator, programmatic
grader, JSON out) → M2 full matrix (embedding retriever, constrained toggle, Gemma path + version
assert) → M3 `chain`/`ambiguous` tiers + LLM judge → M4 markdown report + MLX provider + tests.
