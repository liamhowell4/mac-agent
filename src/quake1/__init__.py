"""Quake-1 — a local macOS assistant built on the tooleval scaffold.

The eval harness (src/tooleval) validated the brain: unsloth Qwen3.5-4B-MTP + fewshot
prompt + greedy decoding over the 104-tool catalog. Quake-1 adds the body: real tool
execution, a terminal REPL, a local daemon, and a Spotlight-style SwiftUI launcher.
"""

__version__ = "0.1.0"
