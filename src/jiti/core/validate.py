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
import sys
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple, cast

from pydantic import ConfigDict, validate_call

from jiti.core.declaration import Gate
from jiti.core.errors import JitiError

# The runtime contract every `@jiti` function (and every candidate exec'd in-process during
# validation) is wrapped with: strict mode forbids pydantic's default coercion, so a buggy
# `parse(123)` against `(text: str)` raises instead of being silently fixed; arbitrary types
# accept plain dataclasses and user classes via isinstance; `validate_return` catches an
# impl that lies about its return type. After `jiti merge` strips the wrapper, this contract
# goes away — by design.
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


class MethodPatch(NamedTuple):
    """The class and method to temporarily bind a candidate onto during method validation."""

    class_name: str
    method: str


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
    patch: MethodPatch | None = None,
    name: str = "",
    module: str = "",
    gates: Sequence[Gate] = (),
    execute: bool = True,
) -> ValidationResult:
    """Lint, type-check, and (unless `execute` is False) run a candidate's tests.

    For a free function, `test_source` calls it by bare name (impl and tests share one
    namespace). For a method, `patch=(ClassName, method)` temporarily binds the candidate
    onto the authored class so the tests' `obj.method(...)` calls reach the candidate.

    `gates` are live tests (from `jiti.required_for`); for each, the target `module.name` is
    bound to the candidate (in the module and in the test's globals) so it exercises the
    candidate however it imported the target. `execute=False` (test-mode) lints and
    type-checks only — used when generating a test against a not-yet-implemented target.
    """
    with TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        impl_file = workdir / "candidate.py"
        impl_file.write_text(impl_source)
        formatted = _format(impl_file)
        lint = (
            _lint("ruff", RUFF, ["check", str(impl_file)], workdir, import_path),
            _lint("ty", TY, ["check", str(impl_file)], workdir, import_path),
            *_ty_on_tests(workdir, test_source, import_path, execute=execute),
        )
        tests = _run_tests(formatted, test_source, patch, name, module, gates) if execute else ()
        checks = (*lint, *tests)
    return ValidationResult(checks=checks, impl_source=formatted)


def _ty_on_tests(
    workdir: Path, test_source: str, import_path: Sequence[str], *, execute: bool
) -> tuple[Check, ...]:
    """Type-check the test file too: a `parse(123)` against `def parse(text: str)` should fail
    statically here, before runtime, so the agent can't "fix" the impl to accept the bad type.

    Tests use the impl by bare name (shared exec namespace), so we prepend `from candidate
    import *` to give ty the same scope. Skipped in test-mode generation (`execute=False`),
    where the candidate is itself a test stub and no companion test source exists yet.
    """
    if not execute or not test_source.strip():
        return ()
    test_file = workdir / "test_candidate.py"
    test_file.write_text("from candidate import *  # noqa: F401, F403\n\n" + test_source)
    return (_lint("ty-tests", TY, ["check", str(test_file)], workdir, import_path),)


def _run_tests(
    impl_source: str,
    test_source: str,
    patch: MethodPatch | None,
    name: str,
    module: str,
    gates: Sequence[Gate],
) -> tuple[Check, ...]:
    namespace: dict[str, object] = {}
    try:
        exec(compile(impl_source, "<jiti-candidate>", "exec", dont_inherit=True), namespace)
        # Wrap the candidate (and only the candidate) with the runtime contract so the tests
        # exec'd next exercise the same validation users get at call time — surfaces type-lie
        # impls as pydantic errors in the agent's feedback rather than wrong-value asserts.
        candidate = namespace.get(name) if name else None
        if callable(candidate):
            namespace[name] = CONTRACT(candidate)
        exec(compile(test_source, "<jiti-candidate-tests>", "exec", dont_inherit=True), namespace)
    except Exception:
        return (Check("tests", ok=False, output=cap(traceback.format_exc())),)
    with _candidate_method(namespace, patch):
        tests = _call_tests(namespace)
        if not gates:
            return (tests,)
        return (tests, _run_gates(namespace.get(name), name, module, gates))


def _run_gates(candidate: object, name: str, module: str, gates: Sequence[Gate]) -> Check:
    if not callable(candidate):
        return Check("gates", ok=False, output="the implementation did not define the target.")
    failures: list[str] = []
    for gate in gates:
        with _bound_to_candidate(gate, name, module, candidate):
            try:
                if gate.test is not None:
                    gate.test()
            except JitiError:
                raise  # a cascade's control-flow error (e.g. a cycle) must not look like a failure
            except _TEST_FAILURES:
                failures.append(f"{gate.name}:\n{traceback.format_exc()}")
    return Check("gates", ok=not failures, output=cap("\n\n".join(failures)))


_MISSING = object()


@contextmanager
def _bound_to_candidate(gate: Gate, name: str, module: str, candidate: object) -> Iterator[None]:
    """Point the target at the candidate however the gate reaches it, then restore.

    Patches the target's module attribute (covers a local `from x import target` or `x.target`)
    and any name in the test's own globals (covers a module-level `from x import target`).
    """
    bindings: list[tuple[dict[str, object], str]] = []
    target_module = sys.modules.get(module)
    if target_module is not None:
        bindings.append((target_module.__dict__, name))
    if gate.test is not None:
        scope = gate.test.__globals__
        bindings.extend((scope, key) for key, value in list(scope.items()) if value is gate.target)
    saved = [(scope, key, scope.get(key, _MISSING)) for scope, key in bindings]
    for scope, key in bindings:
        scope[key] = candidate
    try:
        yield
    finally:
        for scope, key, value in saved:
            if value is _MISSING:
                scope.pop(key, None)
            else:
                scope[key] = value


@contextmanager
def _candidate_method(namespace: dict[str, object], patch: MethodPatch | None) -> Iterator[None]:
    """Bind the candidate onto the authored class for the test run, then restore it.

    Generation is synchronous (the real call is suspended), so this mutation is invisible
    and always undone — `obj.method(...)` in the tests reaches the candidate, not the stub.
    """
    cls = namespace.get(patch.class_name) if patch else None
    if patch is None or not isinstance(cls, type) or patch.method not in namespace:
        yield
        return
    method = patch.method
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
        except _TEST_FAILURES:
            failures.append(f"{name}:\n{traceback.format_exc()}")
    if not ran_any:
        return Check("tests", ok=False, output="no test_* functions were defined")
    return Check("tests", ok=not failures, output=cap("\n\n".join(failures)))


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
