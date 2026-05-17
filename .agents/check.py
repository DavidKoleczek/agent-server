# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Run all project code-quality checks for the ``agentStop`` hook.

Executed by ``.github/hooks/hooks.json`` so that ``ruff format``,
``ruff check --fix``, and ``ty check`` run automatically when an agent
turn ends. If any step fails, the script emits a JSON object of the
form ``{"decision": "block", "reason": "..."}`` on stdout and exits 0;
the Copilot CLI uses that payload to block the stop and surface the
captured tool output back to the agent. On success the script exits 0
with no stdout payload.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent

CHECKS: list[tuple[str, list[str]]] = [
    ("uv run ruff format", ["uv", "run", "ruff", "format"]),
    ("uv run ruff check --fix", ["uv", "run", "ruff", "check", "--fix"]),
    ("uv run ty check", ["uv", "run", "ty", "check"]),
]


def _run(name: str, cmd: list[str], env: dict[str, str]) -> tuple[str, int, str]:
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout or ''}{result.stderr or ''}".strip()
    return name, result.returncode, output


def main() -> int:
    # Strip VIRTUAL_ENV so nested ``uv run`` calls bind to the project's
    # .venv instead of the ephemeral env this PEP 723 script runs under.
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)

    results = [_run(name, cmd, env) for name, cmd in CHECKS]
    failed = [(name, code, output) for name, code, output in results if code != 0]

    if not failed:
        return 0

    reason_parts = [
        "The agentStop hook ran the project's code-quality checks and one or "
        "more steps failed. Fix the issues below before yielding again.",
        "",
    ]
    for name, code, output in failed:
        reason_parts.append(f"--- {name} (exit {code}) ---\n{output}")

    payload = {"decision": "block", "reason": "\n".join(reason_parts)}
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
