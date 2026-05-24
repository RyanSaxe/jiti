"""Validate a candidate implementation: format + lint (ruff), type-check (ty), test (pytest).

Each candidate is written to a throwaway workdir and checked in a subprocess so generated
code never touches the running process. The function under test and its tests are combined
into one module for the pytest run, so tests reference the function directly — jiti adds
the real import only when committing the test section.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

RUFF = ("ruff",)
TY = ("ty",)
PYTEST = (sys.executable, "-m", "pytest")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    output: str


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating one candidate, plus its formatted source to commit."""

    checks: tuple[Check, ...]
    impl_source: str

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def report(self) -> str:
        """Failing-check output, formatted as feedback for the next repair attempt."""
        return "\n\n".join(
            f"[{check.name}]\n{check.output.strip()}" for check in self.checks if not check.ok
        )


def validate(
    impl_source: str,
    test_module: str,
    workdir: Path,
    *,
    import_path: Sequence[str] = (),
) -> ValidationResult:
    """Run ruff, ty, and pytest on a candidate; return checks and the formatted source.

    The impl lives in `candidate.py`; `test_module` is written verbatim as the test file,
    so the caller decides how the tests reach the implementation (a bare import for a free
    function, or importing the class and patching the candidate onto it for a method).
    """
    impl_file = workdir / "candidate.py"
    impl_file.write_text(impl_source)
    formatted = _format(impl_file)

    test_file = workdir / "test_candidate.py"
    test_file.write_text(test_module if test_module.endswith("\n") else test_module + "\n")

    checks = (
        _check("ruff", RUFF, ["check", str(impl_file)], workdir, import_path),
        _check("ty", TY, ["check", str(impl_file)], workdir, import_path),
        _check("pytest", PYTEST, ["-q", str(test_file)], workdir, import_path),
    )
    return ValidationResult(checks=checks, impl_source=formatted)


def _format(impl_file: Path) -> str:
    workdir = impl_file.parent
    _run(RUFF, ["format", str(impl_file)], workdir, ())
    _run(RUFF, ["check", "--fix", str(impl_file)], workdir, ())
    _run(RUFF, ["format", str(impl_file)], workdir, ())
    return impl_file.read_text()


def _check(
    name: str, tool: Sequence[str], args: list[str], workdir: Path, import_path: Sequence[str]
) -> Check:
    code, output = _run(tool, args, workdir, import_path)
    return Check(name=name, ok=code == 0, output=output)


def _run(
    tool: Sequence[str], args: list[str], workdir: Path, import_path: Sequence[str]
) -> tuple[int, str]:
    # PYTHONPATH carries only the workdir (so the candidate is first-party) and the
    # authored package dirs (so import-backs resolve). Injecting the whole sys.path makes
    # ty treat the candidate as a dependency and silently drop its diagnostics; the venv
    # interpreter already supplies stdlib and third-party packages.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(workdir), *import_path])
    process = subprocess.run(
        [*tool, *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )
    return process.returncode, process.stdout + process.stderr
