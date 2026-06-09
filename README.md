# mac-agent / tooleval

A local **tool-calling eval harness** for small models (Qwen 3.5, Gemma 4) on macOS/MCP tasks.
It answers one decision: **fine-tune a small local model, or ship zero-shot with good scaffolding?**

The core insight: zero-shot tool-calling quality is dominated by **scaffolding, not the raw model**.
So scaffolding is a first-class, toggleable variable. Every run is a
`(model × retrieval × decoding × prompt)` **cell**, and the report compares cells per difficulty tier.

See `docs/build-brief.md` for the full spec, `CLAUDE.md` for resolved design decisions, and
`.claude/artifacts/plan-tooleval.html` for the visual roadmap.

## Status

**M1 complete** — skeleton + `single`/`negative` tiers, Ollama provider, passthrough retriever,
deterministic simulator, programmatic grader, agentic loop runner, `(cell,task)` cache, CLI.
Next: M2 (embedding retrieval + constrained decoding + Gemma path + full matrix).

## Quickstart

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
source .venv/bin/activate

# requires a local Ollama (>= 0.20.2) with the model pulled
ollama pull qwen3.5:4b          # or use the default qwen3.5:0.8b
tooleval run --model qwen3.5:4b

pytest                          # adapters + grader tests
```

Outputs land in `results/<timestamp>/` (`raw.jsonl` full traces + `metrics.json`).

## Layout

```
data/tools/catalog.json   103 canonical tool schemas (authored via catalog_builder.html)
data/tasks/*.json         eval tasks, one file per tier
config/run.example.yaml   the run matrix + scaffold knobs
src/tooleval/             providers/ retrieval/ tools/ eval/ report/
```

The catalog is authored in `catalog_builder.html` (open in a browser, edit, Copy JSON). The LLM
judge (M3) is cloud via OpenRouter — set `OPENROUTER_API_KEY` in `.env` (see `.env.example`).
