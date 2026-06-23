# mac-agent — Quake-1

A **local macOS assistant** that controls your Mac through ~100 tools (calendar, mail,
messages, contacts, files, system, music, web, …) using a small model running entirely
on-device. No cloud in the hot path.

The repo holds two things:

- **`quake1/`** — the product. A local agent runtime + a native SwiftUI launcher.
- **`tooleval/`** — the eval harness that was the **testing period for Quake-1**: it ran the
  bake-off that picked the model, prompt, and decoding Quake-1 ships, and it stays as the
  held-out test set + grader for any future fine-tune.

## Quake-1 (the product)

A Spotlight-style launcher (global hotkey) → type a request → the agent plans and executes
real macOS actions, asking before anything destructive.

- **Ship config** (eval-validated): `unsloth/Qwen3.5-4B-MTP` (Q4_K_XL GGUF) via Ollama,
  fewshot prompt, greedy decoding, thinking off. Scored **0.985 judged · chains 14/14 ·
  over-call 0.0 · p50 6.4s** on the harness.
- **Three surfaces:** terminal REPL (`quake`), a daemon (`quake-daemon`, NDJSON over a Unix
  socket), and **Quake1.app** (SwiftUI, Liquid Glass panel, drag-to-move, survives permission
  dialogs).
- **~20 executor domains** under `src/quake1/executors/` (calendar, mail, messages via
  `chat.db`, contacts, files, system, music, web, apps, clipboard, screen, weather, network,
  devices, calls, reminders, shell, notes).
- **Safety model:** read-only tools run free; mutations ask y/N; dangerous ops
  (`shell.run_command`, `system.restart_or_shutdown`, `files.delete`) require typing `yes`.

```bash
quake                       # terminal REPL (in-process, no daemon)
quake-daemon                # socket daemon for the app
```

### Native app

The app is signed with a **stable self-signed identity** so macOS TCC permission grants
(Calendar/Mail/Automation/…) survive rebuilds — see `CLAUDE.md` for the keychain details.

```bash
security unlock-keychain -p quake-signing ~/Library/Keychains/quake-signing.keychain-db
cd app && xcodegen generate && xcodebuild -scheme Quake1 -configuration Release build
```

## tooleval (the harness that validated it)

Every run is a `(model × retrieval × decoding × prompt)` **cell**; the report compares cells
per difficulty tier (single / chain / ambiguous / negative). **M1–M4 complete** — Ollama +
MLX + LiteRT providers, passthrough + embedding retrieval, constrained-decoding toggle,
cloud LLM judge (OpenRouter), markdown/HTML scorecard. A **528-run matrix** drove the ship
decision; see `docs/handoff-2026-06-09.md` for results and caveats.

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]" && source .venv/bin/activate

ollama pull hf.co/unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q4_K_XL   # the ship model
tooleval run --model qwen3.5:4b-mlx                          # single cell
tooleval matrix --config config/run.example.yaml            # full matrix → results/<ts>/
tooleval report --results results/<ts>                       # judge + render scorecard

pytest                                                       # 14 test files
```

## Layout

```
src/quake1/               agent loop, daemon server, safety, executors/ (20 macOS domains)
src/tooleval/             providers/ retrieval/ tools/ eval/ report/
app/Quake1/               native SwiftUI launcher
data/tools/catalog.json   ~104 canonical tool schemas (authored via catalog_builder.html)
data/tasks/*.json         66 eval tasks, one file per tier (held-out sets in data/tasks_holdout*/)
config/*.yaml             run matrices + scaffold knobs
docs/build-brief.md       full spec   ·   docs/handoff-2026-06-09.md   resume-here notes
```

The tool catalog is hand-authored in `catalog_builder.html` (open in a browser, edit, Copy
JSON). The LLM judge is cloud via OpenRouter — set `OPENROUTER_API_KEY` in `.env` (see
`.env.example`). See `CLAUDE.md` for the locked design decisions.
