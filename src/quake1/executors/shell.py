"""shell.* — raw zsh execution. Danger-gating happens at the loop level (safety.py);
this handler just runs the command and reports honestly, including nonzero exits."""

from __future__ import annotations

import subprocess

from ._util import ToolError

TIMEOUT = 30


def run_command(args: dict) -> dict:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ToolError("a shell command is required")
    try:
        p = subprocess.run(
            ["/bin/zsh", "-c", command],  # plain -c: never a login shell
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"command timed out after {TIMEOUT}s") from e
    return {
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
        "exit_code": p.returncode,
    }


HANDLERS = {
    "shell.run_command": run_command,
}
