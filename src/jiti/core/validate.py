"""Validate a candidate: lint + format (ruff), type-check (ty), then test in-process.

ruff and ty run as subprocesses on the candidate written to a temp file. Tests run
**in-process** — the candidate impl and its `test_*` functions are exec'd into one namespace
and called here — so that when a test exercises the function and it calls another `@jiti`
function, that callee's generation cascades in this same process, sharing live state.

(That means generated code executes in-process during generation; jiti is scoped to pure
functions, and the agent's experiments run against copies — see `tools.py`.)
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import subprocess
import sys
import traceback
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, cast

from pydantic import ConfigDict, validate_call

from jiti.core.declaration import Gate
from jiti.core.errors import JitiError

# strict: no silent coercion of bad-typed args. validate_return: catches type-lying impls.
CONTRACT = validate_call(
    config=ConfigDict(strict=True, arbitrary_types_allowed=True),
    validate_return=True,
)

# Invoked through the interpreter so a host app without the venv's bin on PATH still finds them.
RUFF = (sys.executable, "-m", "ruff")
TY = (sys.executable, "-m", "ty")


def _test_failures() -> tuple[type[BaseException], ...]:
    # A failing test raises `Exception` (e.g. AssertionError), but pytest signals some failures
    # (a `pytest.raises` that didn't raise) via BaseException subclasses a plain `except Exception`
    # would miss. Catch those too when pytest is importable; never let its absence or an API
    # change break `import jiti` — fall back to plain Exception.
    try:
        import pytest

        return (Exception, pytest.fail.Exception, pytest.skip.Exception, pytest.xfail.Exception)
    except Exception:
        return (Exception,)


_TEST_FAILURES = _test_failures()


class RoutingTarget(Protocol):
    """A `_JitiCallable`-like object that exposes `routing_to(candidate)` for validation.

    Defined as a Protocol here so `validate.py` (core) doesn't import the decorator module.
    """

    def routing_to(self, candidate: Callable[..., object]) -> AbstractContextManager[None]: ...


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
    name: str | None = None,
    gates: Sequence[Gate] = (),
    routing_target: RoutingTarget | None = None,
    execute: bool = True,
) -> ValidationResult:
    """Lint, type-check, and (unless `execute` is False) run a candidate's tests.

    The candidate impl is exec'd into a fresh namespace; the agent's `test_*` functions are
    exec'd alongside so bare-name calls reach the candidate directly. When `routing_target`
    is provided (the `_JitiCallable` for this declaration), the candidate is bound onto it
    via `routing_to(candidate)` for the duration of the test/gate run — so calls that go
    through any wrapper stack (`obj.method(...)`, gates calling the target by its imported
    name) reach the candidate through the JitiCallable's existing dispatch path, with no
    extra patching of bindings.

    `gates` are live tests (from `jiti.required_for`); each runs after the agent's tests in
    the same routing context. `execute=False` (test-mode) lints and type-checks only — used
    when generating a test against a not-yet-implemented target.
    """
    with TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        impl_file = workdir / "candidate.py"
        impl_file.write_text(impl_source)
        formatted = _format(impl_file)
        lint = (
            _lint("ruff", RUFF, ["check", str(impl_file)], workdir, import_path),
            _lint("ty", TY, ["check", str(impl_file)], workdir, import_path),
            *_ty_on_tests(workdir, formatted, test_source, import_path, execute=execute),
        )
        tests = (
            _run_tests(formatted, test_source, name or "", gates, routing_target) if execute else ()
        )
        checks = (*lint, *tests)
    return ValidationResult(checks=checks, impl_source=formatted)


def _ty_on_tests(
    workdir: Path,
    impl_source: str,
    test_source: str,
    import_path: Sequence[str],
    *,
    execute: bool,
) -> tuple[Check, ...]:
    """Type-check the test file too: a `parse(123)` against `def parse(text: str)` should fail
    statically here, before runtime, so the agent can't "fix" the impl to accept the bad type.

    Tests use the impl by bare name (shared exec namespace), so we explicitly import the
    candidate's public names to give ty the same scope. Skipped in test-mode generation
    (`execute=False`), where the candidate is itself a test stub and no companion test
    source exists yet.
    """
    if not execute or not test_source.strip():
        return ()
    test_file = workdir / "test_candidate.py"
    test_file.write_text(_candidate_import(impl_source) + test_source)
    return (_lint("ty-tests", TY, ["check", str(test_file)], workdir, import_path),)


def _candidate_import(impl_source: str) -> str:
    """Return a `from candidate import a, b` line for the candidate's public top-level names.

    Picks up both definitions (functions/classes) and re-exports — names brought into the
    module namespace via `import X` or `from M import X` — so a test that references an
    imported type (e.g. a dataclass the impl imports back from the host) sees it under ty.
    """
    try:
        tree = ast.parse(impl_source)
    except SyntaxError:
        return ""
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.ImportFrom):
            names.extend(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend((alias.asname or alias.name).split(".", 1)[0] for alias in node.names)
    public = [name for name in dict.fromkeys(names) if not name.startswith("_") and name != "*"]
    if not public:
        return ""
    return f"from candidate import {', '.join(public)}\n\n"


def _run_tests(
    impl_source: str,
    test_source: str,
    name: str,
    gates: Sequence[Gate],
    routing_target: RoutingTarget | None,
) -> tuple[Check, ...]:
    namespace: dict[str, object] = {}
    try:
        exec(compile(impl_source, "<jiti-candidate>", "exec", dont_inherit=True), namespace)
        candidate = namespace.get(name)
        if callable(candidate):  # wrap only the named candidate, not helpers exec'd alongside
            namespace[name] = CONTRACT(candidate)
        exec(compile(test_source, "<jiti-candidate-tests>", "exec", dont_inherit=True), namespace)
    except Exception:
        return (Check("tests", ok=False, output=cap(traceback.format_exc())),)
    contracted = namespace.get(name)
    routing = (
        routing_target.routing_to(cast("Callable[..., object]", contracted))
        if routing_target is not None and callable(contracted)
        else nullcontext()
    )
    with routing:
        tests = _call_tests(namespace)
        if not gates:
            return (tests,)
        return (tests, _run_gates(contracted, gates))


def _run_gates(candidate: object, gates: Sequence[Gate]) -> Check:
    if not callable(candidate):
        return Check("gates", ok=False, output="the implementation did not define the target.")
    failures: list[str] = []
    for gate in gates:
        try:
            if gate.test is not None:
                run_test_callable(gate.test)
        except JitiError:
            raise  # a cascade's control-flow error (e.g. a cycle) must not look like a failure
        except _TEST_FAILURES:
            failures.append(f"{gate.name}:\n{traceback.format_exc()}")
    return Check("gates", ok=not failures, output=cap("\n\n".join(failures)))


def _call_tests(namespace: dict[str, object]) -> Check:
    failures: list[str] = []
    ran_any = False
    for name, value in namespace.items():
        if not (name.startswith("test_") and callable(value)):
            continue
        ran_any = True
        try:
            run_test_callable(cast("Callable[[], object]", value))
        except JitiError:
            raise  # a cascade's control-flow error (e.g. a cycle) must not look like a failure
        except _TEST_FAILURES:
            failures.append(f"{name}:\n{traceback.format_exc()}")
    if not ran_any:
        return Check("tests", ok=False, output="no test_* functions were defined")
    return Check("tests", ok=not failures, output=cap("\n\n".join(failures)))


def run_test_callable(test: Callable[[], object]) -> None:
    result = test()
    if inspect.isawaitable(result):
        _run_awaitable(result)


def _run_awaitable(awaitable: Awaitable[object]) -> object:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(awaitable))
    raise JitiError(
        "async validation cannot run inside an active event loop; trigger async jiti "
        "generation through the decorated async function so resolution can run off-thread."
    )


async def _await_result(awaitable: Awaitable[object]) -> object:
    return await awaitable


def _format(impl_file: Path) -> str:
    workdir = impl_file.parent
    _run(RUFF, ["check", "--fix", str(impl_file)], workdir, ())
    _run(RUFF, ["format", str(impl_file)], workdir, ())
    return impl_file.read_text()


def _lint(
    name: str, tool: Sequence[str], args: list[str], workdir: Path, import_path: Sequence[str]
) -> Check:
    code, output = _run(tool, args, workdir, import_path)
    return Check(name=name, ok=code == 0, output=cap(output))


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


def cap(text: str, limit: int = 4000) -> str:
    """Truncate long tool/check output so it can't blow up the agent's context."""
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"
