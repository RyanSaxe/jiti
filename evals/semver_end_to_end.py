#!/usr/bin/env python3
"""End-to-end eval for the semver example.

What it verifies, in order:
  1. `jiti clear` wipes the mirror.
  2. `python -m examples.semver demo` generates the library (needs ANTHROPIC_API_KEY).
  3. A second `demo` invocation runs without ANTHROPIC_API_KEY (mirror cache hit).
  4. `pytest examples/semver/tests` passes without ANTHROPIC_API_KEY.
  5. `jiti merge --all` inlines the generated impl + tests into source.
  6. `pytest examples/semver/tests` still passes post-merge (no API key needed).
  7. `python -m examples.semver demo` runs post-merge without ANTHROPIC_API_KEY.
  8. Demo output is identical pre-merge vs post-merge.

Invoke from the repo root with ANTHROPIC_API_KEY set:
    uv run python evals/semver_end_to_end.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RED = "\033[31m"
GREEN = "\033[32m"
DIM = "\033[2m"
RESET = "\033[0m"


def step(n: int, label: str) -> None:
    print(f"\n{GREEN}== STEP {n}: {label} =={RESET}")


def run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"{DIM}$ {' '.join(argv)}{RESET}")
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env if env is not None else os.environ.copy(),
        text=True,
        capture_output=capture,
        check=False,
    )
    if capture:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    if check and result.returncode != 0:
        sys.exit(f"{RED}step failed (exit {result.returncode}){RESET}")
    return result


def without_anthropic(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    if extra:
        env.update(extra)
    return env


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(f"{RED}ANTHROPIC_API_KEY must be set for step 2 (initial generation).{RESET}")

    semver_test_dir = "examples/semver/tests"
    demo_cmd = ["uv", "run", "python", "-m", "examples.semver", "demo"]

    step(1, "clear .jiti/")
    run(["uv", "run", "jiti", "clear"])

    step(2, "generate semver via the demo (needs ANTHROPIC_API_KEY)")
    pre = run(demo_cmd, capture=True)

    step(3, "re-run the demo without ANTHROPIC_API_KEY (mirror cache hit)")
    cached = run(demo_cmd, env=without_anthropic(), capture=True)
    if cached.stdout != pre.stdout:
        sys.exit(
            f"{RED}cached run diverged from initial generation output{RESET}\n"
            f"--- initial ---\n{pre.stdout}\n--- cached ---\n{cached.stdout}"
        )

    step(4, "pytest passes without ANTHROPIC_API_KEY (pre-merge)")
    run(["uv", "run", "pytest", semver_test_dir, "-q"], env=without_anthropic())

    step(5, "jiti merge --all")
    run(["uv", "run", "jiti", "merge", "--all"])

    step(6, "pytest passes post-merge (no API key)")
    run(["uv", "run", "pytest", semver_test_dir, "-q"], env=without_anthropic())

    step(7, "demo runs post-merge without ANTHROPIC_API_KEY")
    post = run(demo_cmd, env=without_anthropic(), capture=True)

    step(8, "demo output is identical pre- vs post-merge")
    if pre.stdout != post.stdout:
        sys.exit(
            f"{RED}post-merge demo output diverged from pre-merge{RESET}\n"
            f"--- pre-merge ---\n{pre.stdout}\n--- post-merge ---\n{post.stdout}"
        )

    print(f"\n{GREEN}All 8 steps passed.{RESET}")


if __name__ == "__main__":
    main()
