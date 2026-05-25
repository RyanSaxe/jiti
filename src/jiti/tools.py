"""In-process tools the generation agent calls, bound to the live call context.

State never leaves the process: tools take code/queries and return text. `inspect` reads
live values; `run_python` experiments against deep-copied args (so it can't corrupt the
caller's objects); `submit` validates a candidate. The Python objects themselves are never
sent to the model.
"""

from __future__ import annotations

import copy
import io
import re
import subprocess
import sys
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiti._log import logger
from jiti.declaration import Declaration
from jiti.errors import JitiError
from jiti.validate import MethodPatch, cap, validate

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "inspect",
        "description": (
            "Evaluate a Python expression against the LIVE call (its real arguments, by "
            "parameter name, plus the function's module globals) and return the value's type "
            "and repr. Read-only — use it to learn the real shape of the inputs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"expr": {"type": "string", "description": "Expression to evaluate."}},
            "required": ["expr"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute Python in a scratch namespace seeded with DEEP COPIES of the real "
            "arguments (by parameter name) and the module globals. Print what you want to see. "
            "Use it to experiment with a candidate approach against realistic data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Code to execute."}},
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a project file (e.g. an already-generated sibling).",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "description": "Search the project's Python files for a regex pattern.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "submit",
        "description": (
            "Validate a candidate: ruff + ty on the implementation, then run the tests "
            "in-process. `tests` reference the function by its bare name. Returns PASSED or the "
            "failures to fix. Submit repeatedly until it passes; the last passing one is kept."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "impl": {"type": "string", "description": "The implementation module source."},
                "tests": {
                    "type": "string",
                    "description": "Named test_* functions, bare-name calls.",
                },
            },
            "required": ["impl", "tests"],
        },
    },
]


@dataclass
class CallContext:
    """The live state and helpers a generation agent's tools operate on."""

    declaration: Declaration
    args: tuple[object, ...]
    kwargs: dict[str, object]
    import_path: tuple[str, ...] = ()
    passing: tuple[str, str] | None = None

    def inspect(self, expr: str) -> str:
        value = eval(expr, {**self._module_globals(), **self._bound_args()})
        return f"{type(value).__name__}: {cap(repr(value))}"

    def run_python(self, code: str) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exec(code, self._experiment_namespace())
        return cap(buffer.getvalue()) or "(no output)"

    def read_file(self, path: str) -> str:
        target = Path(path).resolve()
        root = Path.cwd().resolve()
        if target != root and root not in target.parents:
            return f"refused: {path} is outside the project root"
        return cap(target.read_text())

    def grep(self, pattern: str) -> str:
        try:
            found = subprocess.run(
                ["rg", "--line-number", "--no-heading", pattern, "."],
                capture_output=True,
                text=True,
            ).stdout
        except FileNotFoundError:
            found = _python_grep(pattern)
        return cap(found) or "(no matches)"

    def submit(self, impl: str, tests: str) -> str:
        patch = None
        if self.declaration.class_context is not None:
            patch = MethodPatch(self.declaration.class_context.name, self.declaration.name)
        gates = tuple(gate for gate in self.declaration.gates if gate.kind == "human")
        result = validate(
            impl,
            tests,
            import_path=self.import_path,
            patch=patch,
            name=self.declaration.name,
            gates=gates,
        )
        if result.ok:
            self.passing = (result.impl_source, tests)
            return "PASSED — ruff, ty, and tests are all green."
        return f"FAILED:\n{result.report}"

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


def dispatch(context: CallContext, name: str, tool_input: dict[str, Any]) -> str:
    """Run a tool by name with the model-supplied input, returning text (errors included)."""
    handler = getattr(context, name, None)
    if name.startswith("_") or not callable(handler):
        return f"unknown tool: {name}"
    logger.debug("  tool %s", name)
    try:
        return str(handler(**tool_input))
    except JitiError:
        raise  # let jiti's own control-flow errors (e.g. generation cycles) propagate
    except Exception:
        return cap(traceback.format_exc())


def _python_grep(pattern: str) -> str:
    regex = re.compile(pattern)
    lines: list[str] = []
    for path in Path.cwd().rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if regex.search(line):
                lines.append(f"{path}:{number}:{line}")
    return "\n".join(lines)
