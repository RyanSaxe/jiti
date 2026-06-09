"""In-process tools the generation agent calls, bound to the live call context.

State never leaves the process: tools take code/queries and return text. `inspect` reads
live values; `run_python` experiments against deep-copied args (so it can't corrupt the
caller's objects); `submit` validates a candidate. The Python objects themselves are never
sent to the model.
"""

from __future__ import annotations

import copy
import io
import shutil
import subprocess
import sys
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiti.agent.transcript import Recorder
from jiti.core.declaration import Declaration, Gate, splice
from jiti.core.errors import JitiError
from jiti.core.log import logger
from jiti.core.validate import (
    DEFAULT_EXECUTION_TIMEOUT,
    RoutingTarget,
    call_bounded,
    cap,
    validate,
)

_RG_INSTALL_HINT = (
    "jiti's `grep` tool requires `rg` (ripgrep) on PATH. Install:\n"
    "  macOS:         brew install ripgrep\n"
    "  Debian/Ubuntu: apt install ripgrep\n"
    "  Rust:          cargo install ripgrep\n"
    "See https://github.com/BurntSushi/ripgrep#installation"
)
_SG_INSTALL_HINT = (
    "jiti's `sg` tool requires `sg` (ast-grep) on PATH. Install:\n"
    "  macOS:         brew install ast-grep\n"
    "  Debian/Ubuntu: cargo install ast-grep --locked\n"
    "  Rust:          cargo install ast-grep --locked\n"
    "See https://ast-grep.github.io/guide/quick-start.html"
)
_AVAILABLE_CLIS: dict[str, bool] = {}


def _require_cli(binary: str, install_hint: str) -> None:
    if _AVAILABLE_CLIS.get(binary):
        return
    if shutil.which(binary) is None:
        raise JitiError(install_hint)
    _AVAILABLE_CLIS[binary] = True


def _tool(name: str, description: str, **properties: dict[str, str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }


_INSPECT = _tool(
    "inspect",
    "Evaluate a Python expression against the LIVE call (its real arguments, by parameter name, "
    "plus the function's module globals) and return the value's type and repr. Read-only — use "
    "it to learn the real shape of the inputs.",
    expr={"type": "string", "description": "Expression to evaluate."},
)
_RUN_PYTHON = _tool(
    "run_python",
    "Execute Python in a scratch namespace seeded with DEEP COPIES of the real arguments (by "
    "parameter name) and the module globals. Print what you want to see. Use it to experiment "
    "with a candidate approach against realistic data.",
    code={"type": "string", "description": "Code to execute."},
)
_READ_FILE = _tool(
    "read_file",
    "Read a project file (e.g. an already-generated sibling).",
    path={"type": "string"},
)
_GREP = _tool(
    "grep",
    "Search the project's Python files for a regex/substring pattern (ripgrep). For "
    "structural queries (e.g. find all defs matching a shape), prefer `sg`.",
    pattern={"type": "string"},
)
_SG = _tool(
    "sg",
    "Structural code search via ast-grep over the project's Python files. The pattern is an "
    "AST pattern, not regex: use `$NAME` for a single identifier, `$$$` for any sequence. "
    "Example: `def $NAME($$$): $$$` finds every function definition.",
    pattern={"type": "string"},
)
_SUBMIT = _tool(
    "submit",
    "Validate a candidate: ruff + ty on the implementation, then run the tests in-process. "
    "Write the function BODY only — jiti splices the target's signature in for you. Put any "
    "PRIVATE helpers, module-level constants, and imports in `helpers`. `tests` reference the "
    "target by its bare name. Returns PASSED or the failures to fix. Submit repeatedly until "
    "it passes; the last passing one is kept.",
    body={
        "type": "string",
        "description": "The function body, no `def` line. Write it as you would write it "
        "inside the function — indentation is flexible (jiti normalizes it).",
    },
    helpers={
        "type": "string",
        "description": "Module-level imports, constants, and PRIVATE helper definitions "
        "(prefix names with `_`). Empty string if nothing is needed.",
    },
    tests={"type": "string", "description": "Named test_* functions, bare-name calls."},
    quality={
        "type": "integer",
        "description": "Your honest 0-10 rating of the code's quality (readability, structure, "
        "simplicity). Lower the score for: duplication, dead helpers, nesting > 3, a helper "
        "used once that could be inlined, names that don't reveal intent, error paths that "
        "swallow information. A low rating earns one refactor pass before commit.",
    },
)
_SUBMIT_TEST = _tool(
    "submit_test",
    "Validate a candidate test: ruff + ty only (the target isn't implemented yet, so it can't "
    "be run). Returns PASSED or the failures to fix. Submit repeatedly until it passes.",
    impl={"type": "string", "description": "The test function's source (import the target)."},
)

IMPL_TOOLS: list[dict[str, Any]] = [_INSPECT, _RUN_PYTHON, _READ_FILE, _GREP, _SG, _SUBMIT]
TEST_TOOLS: list[dict[str, Any]] = [_READ_FILE, _GREP, _SG, _SUBMIT_TEST]


@dataclass
class CallContext:
    """The live state and helpers a generation agent's tools operate on."""

    declaration: Declaration
    args: tuple[object, ...]
    kwargs: dict[str, object]
    import_path: tuple[str, ...] = ()
    gates: tuple[Gate, ...] = ()
    timeout: float = DEFAULT_EXECUTION_TIMEOUT
    """Wall-clock bound on each in-process execution (a test, gate, or tool experiment)."""
    passing: tuple[str, str] | None = None
    quality: int = 10
    """The last passing candidate's self-reported quality; the engine loop reads it for refactor."""
    recorder: Recorder | None = None
    """Optional transcript recorder; dispatch logs each tool call/result here when set."""
    target: RoutingTarget | None = None
    """The `_JitiCallable` for this declaration. During validation, the candidate is bound to
    its `_impl` so any call routed through the wrapper stack (gate tests, agent tests using
    `obj.method(...)`) short-circuits to the candidate instead of re-entering generation."""

    def inspect(self, expr: str) -> str:
        namespace = {**self._module_globals(), **self._bound_args()}
        value = call_bounded(lambda: eval(expr, namespace), self.timeout, "inspect")
        return f"{type(value).__name__}: {cap(repr(value))}"

    def run_python(self, code: str) -> str:
        buffer = io.StringIO()

        def experiment() -> None:
            with redirect_stdout(buffer):
                exec(code, self._experiment_namespace())

        call_bounded(experiment, self.timeout, "run_python")
        return cap(buffer.getvalue()) or "(no output)"

    def read_file(self, path: str) -> str:
        target = Path(path).resolve()
        root = Path.cwd().resolve()
        if target != root and root not in target.parents:
            return f"refused: {path} is outside the project root"
        return cap(target.read_text())

    def grep(self, pattern: str) -> str:
        _require_cli("rg", _RG_INSTALL_HINT)
        found = subprocess.run(
            ["rg", "--line-number", "--no-heading", pattern, "."],
            capture_output=True,
            text=True,
        ).stdout
        return cap(found) or "(no matches)"

    def sg(self, pattern: str) -> str:
        _require_cli("sg", _SG_INSTALL_HINT)
        found = subprocess.run(
            ["sg", "run", "--pattern", pattern, "--lang", "python", "."],
            capture_output=True,
            text=True,
        ).stdout
        return cap(found) or "(no matches)"

    def submit(self, body: str, helpers: str, tests: str, quality: int = 10) -> str:
        try:
            impl = splice(self.declaration, body, helpers)
        except JitiError as exc:
            return f"FAILED:\n[contract]\n{exc}"
        result = validate(
            impl,
            tests,
            import_path=self.import_path,
            name=self.declaration.name,
            gates=self.gates,
            routing_target=self.target,
            timeout=self.timeout,
            uses=self.declaration.uses,
        )
        if not result.ok:
            return f"FAILED:\n{result.report}"
        self.passing = (result.impl_source, tests)
        self.quality = quality
        return "PASSED — ruff, ty, and tests are all green."

    def submit_test(self, impl: str) -> str:
        result = validate(impl, "", import_path=self.import_path, execute=False)
        if not result.ok:
            return f"FAILED:\n{result.report}"
        self.passing = (result.impl_source, "")
        return "PASSED — ruff and ty are green."

    def _bound_args(self) -> dict[str, object]:
        try:
            bound = self.declaration.signature.bind(*self.args, **self.kwargs)
        except TypeError:
            return {}
        bound.apply_defaults()
        return dict(bound.arguments)

    def _module_globals(self) -> dict[str, object]:
        module = sys.modules.get(self.declaration.module)
        return dict(vars(module)) if module else {}

    def _experiment_namespace(self) -> dict[str, object]:
        namespace = self._module_globals()
        for name, value in self._bound_args().items():
            try:
                namespace[name] = copy.deepcopy(value)
            except Exception:
                namespace[name] = value
        return namespace


def dispatch(
    context: CallContext, name: str, tool_input: dict[str, Any], parse_error: str | None = None
) -> str:
    """Run a tool by name with the model-supplied input, returning text (errors included).

    `parse_error` short-circuits with a tool-failure message when the model's JSON
    arguments were malformed — that way a truncated or malformed tool call comes back to
    the agent as something to fix on the next turn, not as an exception out of the user's
    `@jiti` call.
    """
    if parse_error is not None:
        return f"FAILED:\n{parse_error}\nResend the tool call with valid JSON arguments."
    handler = getattr(context, name, None)
    if name.startswith("_") or not callable(handler):
        return f"unknown tool: {name}"
    logger.debug("  tool %s %s", name, _format_tool_input(tool_input))
    if context.recorder is not None:
        context.recorder.tool_call(name, tool_input)
    try:
        result = str(handler(**tool_input))
    except JitiError:
        raise  # let jiti's own control-flow errors (e.g. generation cycles) propagate
    except Exception:
        result = cap(traceback.format_exc())
    logger.debug("    -> %s", _format_tool_result(result))
    if context.recorder is not None:
        context.recorder.tool_result(name, result)
    return result


def _format_tool_input(tool_input: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in tool_input.items():
        rendered = repr(value)
        if len(rendered) > 80:
            rendered = rendered[:77] + "..."
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def _format_tool_result(result: str) -> str:
    snippet = result.replace("\n", " | ")
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    return snippet
