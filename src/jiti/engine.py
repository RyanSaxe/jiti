"""The agentic generation engine: an in-process Anthropic tool-use loop, per `@jiti` call.

On a call that needs generation, the engine runs Claude with the in-process tools, lets it
inspect real values / explore / experiment / submit until validation is green, then commits.
Generation cascades naturally: a candidate's in-process tests call other `@jiti` functions,
whose wrappers re-enter this same (shared) engine. A cycle guard stops infinite recursion.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiti.declaration import Declaration
from jiti.errors import ConflictError, GenerationCycleError, GenerationError, JitiError
from jiti.store import Action, JitiStore
from jiti.tools import TOOL_SCHEMAS, CallContext, dispatch

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_TURNS = 40


@dataclass
class Engine:
    """Generates implementations via an agent loop and commits them to the store.

    `client` is an `anthropic.Anthropic`-like object (only `.messages.create(...)` is used);
    tests inject a fake. A single shared engine backs all `@jiti` functions so the cycle
    guard and store stay consistent across a cascade.
    """

    client: Any
    store: JitiStore
    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_turns: int = DEFAULT_MAX_TURNS
    _in_progress: set[str] = field(default_factory=set)

    def implement(
        self, declaration: Declaration, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> Any:
        resolution = self.store.resolve(declaration)
        if resolution.action is Action.CONFLICT:
            raise ConflictError(
                f"{declaration.key}: the implementation was hand-edited and the declaration "
                "has since changed. Reconcile them before running."
            )
        if resolution.action in (Action.GENERATE, Action.REGENERATE):
            self._generate(declaration, args, kwargs)
        return self.store.load(declaration)

    def _generate(
        self, declaration: Declaration, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> None:
        if declaration.class_context is not None:
            raise JitiError(
                f"{declaration.key}: the agentic engine generates free functions only for now; "
                "method support is the next step."
            )
        key = declaration.key
        if key in self._in_progress:
            raise GenerationCycleError(
                f"generation cycle: {key} is needed to generate itself (mutually recursive stubs)."
            )
        self._in_progress.add(key)
        try:
            context = CallContext(declaration, args, kwargs, import_path=_import_path(declaration))
            self._run_agent(declaration, context)
            if context.passing is None:
                raise GenerationError(
                    f"{key}: the agent finished without an implementation that passes validation."
                )
            impl, tests = context.passing
            self.store.write(declaration, impl, _committed_tests(declaration, tests))
        finally:
            self._in_progress.discard(key)

    def _run_agent(self, declaration: Declaration, context: CallContext) -> None:
        system = [{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}]
        messages: list[dict[str, Any]] = [{"role": "user", "content": _task_prompt(declaration)}]
        for _ in range(self.max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if _is_tool_use(block)]
            if not tool_uses:
                return
            messages.append(
                {"role": "user", "content": [_tool_result(context, block) for block in tool_uses]}
            )


def _is_tool_use(block: Any) -> bool:
    return getattr(block, "type", None) == "tool_use"


def _tool_result(context: CallContext, block: Any) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": dispatch(context, block.name, block.input),
    }


def default_engine() -> Engine:
    """The shared engine backing bare `@jiti` (built lazily so importing jiti needs no key)."""
    global _DEFAULT
    if _DEFAULT is None:
        import anthropic

        _DEFAULT = Engine(client=anthropic.Anthropic(), store=JitiStore(Path.cwd() / ".jiti"))
    return _DEFAULT


_DEFAULT: Engine | None = None


def _committed_tests(declaration: Declaration, tests: str) -> str:
    return f"from {declaration.module} import {declaration.name}\n\n{tests}"


def _import_path(declaration: Declaration) -> tuple[str, ...]:
    module = sys.modules.get(declaration.module)
    file = getattr(module, "__file__", None)
    if file is None:
        return ()
    depth = len(declaration.module.split("."))
    return (str(Path(file).resolve().parents[depth - 1]),)


def _task_prompt(declaration: Declaration) -> str:
    lines = [
        f"Implement `{declaration.name}`.",
        "",
        f"Signature: def {declaration.name}{declaration.signature}",
    ]
    if declaration.docstring:
        lines.append(f"Description: {declaration.docstring}")
    if declaration.hint:
        lines.append(f"Author hint:\n{declaration.hint}")
    symbols = ", ".join(declaration.available_symbols) or "(none)"
    lines.append(
        f"You may import these module-level symbols from `{declaration.module}`: {symbols}"
    )
    lines.append("")
    lines.append("Inspect the real arguments first, then implement and submit until it passes.")
    return "\n".join(lines)


_SYSTEM = """You are jiti's code-generation agent. You implement ONE Python function from \
its interface, then prove it works — this is committed source, not a sketch.

You run INSIDE the live process at the call site. Use your tools:
- inspect(expr): read the real arguments (by parameter name) and module globals.
- run_python(code): experiment against deep copies of the real arguments.
- read_file(path) / grep(pattern): explore the codebase for conventions and helpers.
- submit(impl, tests): validate a candidate (ruff + ty + your tests, run in-process). \
Iterate until it returns PASSED, then stop.

Rules:
- Write real, idiomatic, correct Python.
- The implementation must define exactly the target function with the given signature, plus \
any PRIVATE helpers (prefix them `_<name>__`).
- Tests are named `test_*` functions that call the target by its BARE name — do not import \
it; it shares their namespace during validation. Cover real behavior and edge cases.
- Assume the function is pure (no side effects)."""
