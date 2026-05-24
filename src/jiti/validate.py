"""Validate a candidate: lint + format (ruff), type-check (ty), then test in-process.

ruff and ty run as subprocesses on the candidate written to a temp file. Tests run
**in-process** — the candidate impl and its `test_*` functions are exec'd into one namespace
and called here — so that when a test exercises the function and it calls another `@jiti`
function, that callee's generation cascades in this same process, sharing live state.

(That means generated code executes in-process during generation; jiti is scoped to pure
functions, and the agent's experiments run against copies — see `tools.py`.)
"""

from __future__ import annotations

import os
import subprocess
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from jiti.errors import JitiError

RUFF = ("ruff",)
TY = ("ty",)
_MAX_OUTPUT = 4000


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
        """Failing-check output, formatted as feedback for the next attempt."""
        return "\n\n".join(
            f"[{check.name}]\n{check.output.strip()}" for check in self.checks if not check.ok
        )


def validate(
    impl_source: str,
    test_source: str,
    *,
    import_path: Sequence[str] = (),
    patch: tuple[str, str] | None = None,
) -> ValidationResult:
    """Lint, type-check, and run a candidate's tests; return checks and the formatted source.

    For a free function, `test_source` calls it by bare name (impl and tests share one
    namespace). For a method, `patch=(ClassName, method)` temporarily binds the candidate
    onto the authored class so the tests' `obj.method(...)` calls reach the candidate.
    """
    with TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        impl_file = workdir / "candidate.py"
        impl_file.write_text(impl_source)
        formatted = _format(impl_file)
        checks = (
            _lint("ruff", RUFF, ["check", str(impl_file)], workdir, import_path),
            _lint("ty", TY, ["check", str(impl_file)], workdir, import_path),
            _run_tests(formatted, test_source, patch),
        )
    return ValidationResult(checks=checks, impl_source=formatted)


def _run_tests(impl_source: str, test_source: str, patch: tuple[str, str] | None) -> Check:
    namespace: dict[str, object] = {}
    try:
        exec(compile(impl_source, "<jiti-candidate>", "exec"), namespace)
        exec(compile(test_source, "<jiti-candidate-tests>", "exec"), namespace)
    except Exception:
        return Check("tests", ok=False, output=_cap(traceback.format_exc()))
    with _candidate_method(namespace, patch):
        return _call_tests(namespace)


@contextmanager
def _candidate_method(
    namespace: dict[str, object], patch: tuple[str, str] | None
) -> Iterator[None]:
    """Bind the candidate onto the authored class for the test run, then restore it.

    Generation is synchronous (the real call is suspended), so this mutation is invisible
    and always undone — `obj.method(...)` in the tests reaches the candidate, not the stub.
    """
    cls = namespace.get(patch[0]) if patch else None
    if patch is None or not isinstance(cls, type) or patch[1] not in namespace:
        yield
        return
    method = patch[1]
    missing = object()
    original = cls.__dict__.get(method, missing)
    setattr(cls, method, namespace[method])
    try:
        yield
    finally:
        if original is missing:
            delattr(cls, method)
        else:
            setattr(cls, method, original)


def _call_tests(namespace: dict[str, object]) -> Check:
    failures: list[str] = []
    ran_any = False
    for name, value in namespace.items():
        if not (name.startswith("test_") and callable(value)):
            continue
        ran_any = True
        try:
            cast("Callable[[], object]", value)()
        except JitiError:
            raise  # a cascade's control-flow error (e.g. a cycle) must not look like a failure
        except Exception:
            failures.append(f"{name}:\n{traceback.format_exc()}")
    if not ran_any:
        return Check("tests", ok=False, output="no test_* functions were defined")
    return Check("tests", ok=not failures, output=_cap("\n\n".join(failures)))


def _format(impl_file: Path) -> str:
    workdir = impl_file.parent
    _run(RUFF, ["check", "--fix", str(impl_file)], workdir, ())
    _run(RUFF, ["format", str(impl_file)], workdir, ())
    return impl_file.read_text()


def _lint(
    name: str, tool: Sequence[str], args: list[str], workdir: Path, import_path: Sequence[str]
) -> Check:
    code, output = _run(tool, args, workdir, import_path)
    return Check(name=name, ok=code == 0, output=_cap(output))


def _run(
    tool: Sequence[str], args: list[str], workdir: Path, import_path: Sequence[str]
) -> tuple[int, str]:
    # PYTHONPATH carries only the workdir (so the candidate is first-party) and the authored
    # package dirs (so import-backs resolve). Injecting the whole sys.path makes ty treat the
    # candidate as a dependency and silently drop its diagnostics.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(workdir), *import_path])
    process = subprocess.run([*tool, *args], cwd=workdir, capture_output=True, text=True, env=env)
    return process.returncode, process.stdout + process.stderr


def _cap(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n… (truncated)"
