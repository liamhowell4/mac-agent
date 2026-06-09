"""tooleval CLI. M1: `tooleval run` scores one model+passthrough on the seed tasks."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .config import build_provider, build_retriever, expand_cells, load_config, load_dotenv
from .eval.metrics import aggregate
from .eval.runner import Runner
from .eval.task import load_tasks
from .providers.ollama import (
    OllamaProvider,
    OllamaVersionError,
    assert_ollama_version,
    unload_all,
)
from .retrieval.passthrough import PassthroughRetriever
from .tools.catalog import load_catalog


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def cmd_run(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    tasks = load_tasks(args.tasks)
    if not tasks:
        print(f"No tasks matched: {args.tasks}", file=sys.stderr)
        return 1

    try:
        version = assert_ollama_version(args.host)
    except OllamaVersionError as e:
        print(f"[ollama] {e}", file=sys.stderr)
        return 2
    print(
        f"[ollama {version}] model={args.model}  "
        f"catalog={len(catalog)} tools  tasks={len(tasks)}"
    )

    provider = OllamaProvider(args.model, host=args.host, seed=args.seed)
    retriever = PassthroughRetriever()
    runner = Runner(
        provider, retriever, catalog,
        seed=args.seed, k=args.k,
        cache_dir=None if args.no_cache else Path("results/cache"),
    )

    out_dir = Path(args.out) / _timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_fp = out_dir / "raw.jsonl"

    records = []
    try:
        with raw_fp.open("w") as raw:
            for i, task in enumerate(tasks, 1):
                rec = runner.run_task(task)
                records.append(rec)
                row = {"cell": asdict(runner.cell), **asdict(rec)}
                raw.write(json.dumps(row) + "\n")
                flag = "·cache" if rec.cached else ""
                mark = "PASS" if rec.grade.passed else "FAIL"
                print(f"  [{i:>2}/{len(tasks)}] {task.id:<14} {task.tier:<9} {mark} "
                      f"({rec.turns}t, {rec.latency_s:.1f}s){flag}")
    finally:
        freed = unload_all(args.host)  # take down ALL models when done
        if freed:
            print(f"[cleanup] unloaded: {', '.join(freed)}")

    metrics = aggregate(records)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    _print_headline(runner.cell.label(), metrics)
    print(f"\nResults: {out_dir}/  (raw.jsonl, metrics.json)")
    return 0


def _print_headline(cell_label: str, metrics: dict) -> None:
    o = metrics["overall"]
    print("\n" + "=" * 64)
    print(f"CELL: {cell_label}")
    print("=" * 64)
    tsa = o["tool_selection_accuracy"]
    oc = o["overcall_rate"]
    print(f"  tool_selection_accuracy : {tsa if tsa is not None else '—'}")
    print(f"  overcall_rate (headline): {oc if oc is not None else '—'}   (lower is better)")
    print(f"  task_pass_rate          : {o['task_pass_rate']}")
    print(f"  arg_correctness_rate    : {o['arg_correctness_rate']}")
    print(f"  latency_s p50/p95       : {o['latency_s_p50']} / {o['latency_s_p95']}")
    print("\n  per tier:")
    for tier, b in metrics["per_tier"].items():
        extra = ""
        if b["overcall_rate"] is not None:
            extra = f"  overcall={b['overcall_rate']}"
        elif b["tool_selection_accuracy"] is not None:
            extra = f"  tool_sel={b['tool_selection_accuracy']}"
        print(f"    {tier:<9} n={b['n']:<3} pass={b['task_pass_rate']}{extra}")


def cmd_matrix(args: argparse.Namespace) -> int:
    load_dotenv()
    config = load_config(args.config)
    catalog = load_catalog(args.catalog)
    tasks = load_tasks(config.get("tasks", "data/tasks/*.json"))
    if not tasks:
        print("No tasks matched.", file=sys.stderr)
        return 1
    seed = int(config.get("seed", 42))

    try:
        version = assert_ollama_version(args.host)
    except OllamaVersionError as e:
        print(f"[ollama] {e}", file=sys.stderr)
        return 2

    combos = expand_cells(config)
    print(f"[ollama {version}] catalog={len(catalog)} tools  tasks={len(tasks)}  "
          f"cells={len(combos)}")

    out_dir = Path(args.out) / _timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_fp = out_dir / "raw.jsonl"

    cells_out = []
    prev_provider = None  # unload models when switching, so they don't co-reside on 16GB
    try:
        with raw_fp.open("w") as raw:
            for ci, (mspec, rspec, decoding, _prompt) in enumerate(combos, 1):
                provider = build_provider(mspec, args.host, seed)
                if prev_provider is not None and prev_provider.name != provider.name:
                    print(f"   (unloading {prev_provider.name})")
                    prev_provider.unload()
                prev_provider = provider
                retriever, k = build_retriever(rspec, args.host)
                constrained = decoding == "constrained"
                runner = Runner(
                    provider, retriever, catalog,
                    seed=seed, k=k or args.k, constrained=constrained,
                    cache_dir=None if args.no_cache else Path("results/cache"),
                )
                label = runner.cell.label()
                print(f"\n[cell {ci}/{len(combos)}] {label}")
                records = []
                for task in tasks:
                    rec = runner.run_task(task)
                    records.append(rec)
                    raw.write(json.dumps({"cell": asdict(runner.cell), **asdict(rec)}) + "\n")
                metrics = aggregate(records)
                cells_out.append({"cell": asdict(runner.cell), "metrics": metrics})
                o = metrics["overall"]
                print(f"   pass={o['task_pass_rate']}  tool_sel={o['tool_selection_accuracy']}  "
                      f"overcall={o['overcall_rate']}  recall={o['retrieval_recall']}")
    finally:
        freed = unload_all(args.host)  # take down ALL models (incl. embed) when done
        if freed:
            print(f"\n[cleanup] unloaded: {', '.join(freed)}")

    (out_dir / "matrix.json").write_text(json.dumps(cells_out, indent=2))
    _print_matrix(cells_out)
    print(f"\nResults: {out_dir}/  (raw.jsonl, matrix.json)")
    return 0


def _print_matrix(cells_out: list[dict]) -> None:
    print("\n" + "=" * 88)
    print(f"{'cell':<46}{'pass':>7}{'tool_sel':>10}{'overcall':>10}{'recall':>9}")
    print("-" * 88)
    for c in cells_out:
        o = c["metrics"]["overall"]
        label = c["cell"]["model"] + " | " + c["cell"]["retrieval"] + " | " + c["cell"]["decoding"]

        def f(x):
            return "—" if x is None else f"{x}"
        print(f"{label[:45]:<46}{f(o['task_pass_rate']):>7}{f(o['tool_selection_accuracy']):>10}"
              f"{f(o['overcall_rate']):>10}{f(o['retrieval_recall']):>9}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tooleval")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the eval on seed tasks (M1: one model, passthrough)")
    run.add_argument("--model", default="qwen3.5:0.8b", help="Ollama model tag")
    run.add_argument("--host", default="http://localhost:11434")
    run.add_argument("--catalog", default="data/tools/catalog.json")
    run.add_argument("--tasks", default="data/tasks/*.json")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--k", type=int, default=8, help="top-k (unused for passthrough)")
    run.add_argument("--out", default="results")
    run.add_argument("--no-cache", action="store_true")
    run.set_defaults(func=cmd_run)

    mat = sub.add_parser("matrix", help="run the full cell matrix from a config (M2)")
    mat.add_argument("--config", default="config/run.example.yaml")
    mat.add_argument("--host", default="http://localhost:11434")
    mat.add_argument("--catalog", default="data/tools/catalog.json")
    mat.add_argument("--seed", type=int, default=42)
    mat.add_argument("--k", type=int, default=8)
    mat.add_argument("--out", default="results")
    mat.add_argument("--no-cache", action="store_true")
    mat.set_defaults(func=cmd_matrix)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
