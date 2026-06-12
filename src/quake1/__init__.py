"""Quake-1 — a local macOS assistant built on the tooleval scaffold.

The eval harness (src/tooleval) validated the brain: unsloth Qwen3.5-4B-MTP + fewshot
prompt + greedy decoding over the 104-tool catalog. Quake-1 adds the body: real tool
execution, a terminal REPL, a local daemon, and a Spotlight-style SwiftUI launcher.
"""

__version__ = "0.1.0"

# Ship configuration — the single home for product defaults (eval-validated:
# 0.985 judged / chains 14/14 / overcall 0.0 at p50 6.4s with think off).
SHIP_MODEL = "hf.co/unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q4_K_XL"
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_THINK = False
KEEP_ALIVE = "60m"
