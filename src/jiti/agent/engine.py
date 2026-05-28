"""The agentic generation engine: an in-process Anthropic tool-use loop, per `@jiti` call.

On a call that needs generation, the engine runs Claude with the in-process tools, lets it
inspect real values / explore / experiment / submit until validation is green, then commits.
Generation cascades naturally: a candidate's in-process tests call other `@jiti` functions,
whose wrappers re-enter this same (shared) engine. A cycle guard stops infinite recursion.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import anthropic

from jiti.agent.prompts import STYLE_GUIDE, SYSTEM_PROMPT, TEST_GUIDE, TEST_MODE_PROMPT
from jiti.agent.tools import IMPL_TOOLS, TEST_TOOLS, CallContext, dispatch
from jiti.agent.transcript import Recorder, transcript_path
from jiti.core.declaration import ClassContext, Declaration, Gate, introspect
from jiti.core.discovery import import_test_modules
from jiti.core.errors import ConflictError, GenerationCycleError, GenerationError
from jiti.core.log import cost, log_done, log_llm_call, log_start, record_generation
from jiti.core.models import DEFAULT_MODEL, Model, resolve_default
from jiti.core.store import Action, JitiStore, scratch_rename

DEFAULT_MAX_TOKENS = 8192
DEFAULT_MAX_TURNS = 40
DEFAULT_QUALITY_THRESHOLD = 7
DEFAULT_MAX_REFACTOR = 1


@dataclass
class Engine:
    """Generates implementations via an agent loop and commits them to the store.

    `client` is an `anthropic.Anthropic`-like object (only `.messages.create(...)` is used);
    tests inject a fake. A single shared engine backs all `@jiti` functions so the cycle
    guard and store stay consistent across a cascade.
    """

    client: Any
    store: JitiStore
    model: Model = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_turns: int = DEFAULT_MAX_TURNS
    style: str = STYLE_GUIDE
    test_guide: str = TEST_GUIDE
    quality_threshold: int = DEFAULT_QUALITY_THRESHOLD
    max_refactor: int = DEFAULT_MAX_REFACTOR
    test_paths: tuple[str, ...] | None = None
    """Where to find gates: None scans the tree, a tuple narrows it, () disables discovery."""

    _in_progress: set[str] = field(default_factory=set)
    _discovered: bool = field(default=False)

    def discover(self) -> None:
        """Import the project's test modules once so their gates register before generation."""
        if self._discovered:
            return
        self._discovered = True
        import_test_modules(self.test_paths)

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
        key = declaration.key
        if key in self._in_progress:
            raise GenerationCycleError(
                f"generation cycle: {key} is needed to generate itself (mutually recursive stubs)."
            )
        self._in_progress.add(key)
        depth = len(self._in_progress)
        log_start(key, depth)
        started = perf_counter()
        recorder = Recorder()
        try:
            gates = self._prepare_gates(declaration)
            context = CallContext(
                declaration,
                args,
                kwargs,
                import_path=_import_path(declaration),
                gates=gates,
                recorder=recorder,
            )
            total_cost = self._run_agent(
                context,
                self._system_blocks(),
                _task_prompt(declaration),
                IMPL_TOOLS,
                threshold=self.quality_threshold,
                max_refactor=self.max_refactor,
            )
            if context.passing is None:
                raise GenerationError(
                    f"{key}: the agent finished without an implementation that passes validation."
                )
            impl, tests = context.passing
            self.store.write(declaration, impl, _committed_tests(declaration, tests))
            log_done(key, depth, perf_counter() - started, total_cost)
            record_generation(total_cost)
        finally:
            recorder.write(transcript_path(self.store.root, declaration.module, declaration.name))
            self._in_progress.discard(key)

    def generate_test(self, test: Declaration, target: Declaration) -> None:
        """Generate a jiti-test body from `target`'s interface (TDD), validated by ruff + ty."""
        if (section := self.store.read_test_section(test)) and section.spec_hash == test.spec_hash:
            return
        key = test.key
        if key in self._in_progress:
            raise GenerationCycleError(f"generation cycle: {key} is needed to generate itself.")
        self._in_progress.add(key)
        depth = len(self._in_progress)
        log_start(key, depth)
        started = perf_counter()
        recorder = Recorder()
        try:
            context = CallContext(test, (), {}, import_path=_import_path(test), recorder=recorder)
            task = _test_task_prompt(test, target)
            # threshold=0 → no refactor pass for tests (red→green→refactor is for the impl).
            cost_ = self._run_agent(
                context, self._test_system_blocks(), task, TEST_TOOLS, threshold=0, max_refactor=0
            )
            if context.passing is None:
                raise GenerationError(f"{key}: the agent finished without a passing test.")
            body, _ = context.passing
            self.store.write_test(test, body)
            log_done(key, depth, perf_counter() - started, cost_)
            record_generation(cost_)
        finally:
            recorder.write(transcript_path(self.store.root, test.module, test.name))
            self._in_progress.discard(key)

    def run_test(self, test: Declaration, target: Declaration) -> types.FunctionType:
        """Ensure a jiti-test is generated, then return its committed body to run."""
        self.generate_test(test, target)
        return self.store.load_test(test)

    def _prepare_gates(self, declaration: Declaration) -> tuple[Gate, ...]:
        # Human gates run as-is; jiti-test gates are generated (test-mode) then loaded so both
        # run against the candidate via the same rebinding path.
        runnable: list[Gate] = []
        for gate in declaration.gates:
            if gate.kind == "human" or gate.test is None:
                runnable.append(gate)
                continue
            test_decl = introspect(gate.test)
            loaded = self.run_test(test_decl, declaration)
            runnable.append(Gate(gate.name, "jiti", gate.spec, test=loaded, target=gate.target))
        return tuple(runnable)

    def _run_agent(
        self,
        context: CallContext,
        system: list[dict[str, Any]],
        task: str,
        tools: list[dict[str, Any]],
        *,
        threshold: int,
        max_refactor: int,
    ) -> float:
        depth = len(self._in_progress)
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        total_cost = 0.0
        refactors = 0
        for turn in range(1, self.max_turns + 1):
            started = perf_counter()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
            usage = getattr(response, "usage", None)
            key = context.declaration.key
            elapsed = perf_counter() - started
            log_llm_call(key, turn, depth, elapsed, usage, self.model)
            spent = cost(self.model, usage) or 0.0
            total_cost += spent
            if context.recorder is not None:
                context.recorder.turn(turn, elapsed, usage, spent, response.content)
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if _is_tool_use(block)]
            if not tool_uses:
                return total_cost
            results = [_tool_result(context, block) for block in tool_uses]
            if context.passing is not None:
                if context.quality >= threshold or refactors >= max_refactor:
                    return total_cost  # green and polished enough — skip the model's wrap-up turn
                refactors += 1
                results.append(_refactor_nudge(context.quality, threshold))
            messages.append({"role": "user", "content": results})
        return total_cost

    def _system_blocks(self) -> list[dict[str, Any]]:
        # Style and test guidance are separate cached blocks so jiti's mechanical rules stay
        # cacheable independent of whichever guides are in effect.
        blocks = [_cached(SYSTEM_PROMPT)]
        if self.style.strip():
            house_style = f"Follow this house style in the code you write:\n\n{self.style}"
            blocks.append(_cached(house_style))
        if self.test_guide.strip():
            guidance = f"Follow this guidance when writing tests:\n\n{self.test_guide}"
            blocks.append(_cached(guidance))
        return blocks

    def _test_system_blocks(self) -> list[dict[str, Any]]:
        blocks = [_cached(TEST_MODE_PROMPT)]
        if self.test_guide.strip():
            blocks.append(_cached(f"Follow this guidance when writing tests:\n\n{self.test_guide}"))
        return blocks


def _cached(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _refactor_nudge(quality: int, threshold: int) -> dict[str, Any]:
    return {
        "type": "text",
        "text": (
            f"That passes, but you rated quality {quality} < {threshold}. Refactor for "
            "readability, structure, and simplicity while keeping every test green, then resubmit."
        ),
    }


def _is_tool_use(block: Any) -> bool:
    return getattr(block, "type", None) == "tool_use"


def _tool_result(context: CallContext, block: Any) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": dispatch(context, block.name, block.input),
    }


class _LazyAnthropic:
    """An Anthropic client that builds the real one on first use.

    Constructing `anthropic.Anthropic()` requires an API key, but `default_engine()` runs on
    every first `@jiti` call — including cached ones that never reach the model. Deferring the
    construction to the first `messages` access keeps importing jiti and running committed code
    key-free; only generation (which calls the model) needs `ANTHROPIC_API_KEY`.
    """

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    @property
    def messages(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client.messages


def default_engine() -> Engine:
    """The shared engine backing bare `@jiti` (built lazily so importing jiti needs no key).

    Honors the `JITI_MODEL` env var to pick a cheaper model than Opus when set.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Engine(
            client=_LazyAnthropic(),
            store=JitiStore(Path.cwd() / ".jiti"),
            model=resolve_default(),
        )
    return _DEFAULT


_DEFAULT: Engine | None = None


def _committed_tests(declaration: Declaration, tests: str) -> str:
    # The agent's own tests are committed as prunable `scratch` (the author's jiti-tests, written
    # via write_test, keep their names). A method's tests already import the class and call
    # `obj.method(...)`; a free function's tests use the bare name, so prepend the import.
    scratch = scratch_rename(tests)
    if declaration.class_context is not None:
        return scratch
    return f"from {declaration.module} import {declaration.name}\n\n{scratch}"


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
    if declaration.class_context is not None:
        lines.append(_class_section(declaration.class_context))
    symbols = ", ".join(declaration.available_symbols) or "(none)"
    lines.append(
        f"You may import these module-level symbols from `{declaration.module}`: {symbols}"
    )
    lines.append("")
    lines.extend(_test_instruction(declaration))
    gate_section = _gate_section(declaration.gates)
    if gate_section:
        lines.append(gate_section)
    lines.append("Inspect the real arguments first, then implement and submit until it passes.")
    return "\n".join(lines)


def _test_task_prompt(test: Declaration, target: Declaration) -> str:
    lines = [
        f"Write the test function `{test.name}`.",
        "",
        "It tests this target, which is NOT implemented yet — write the test against its contract:",
        f"  def {target.name}{target.signature}",
    ]
    if target.docstring:
        lines.append(f'  """{target.docstring}"""')
    if test.docstring:
        lines.append(f"\nThis test must verify: {test.docstring}")
    symbols = ", ".join(target.available_symbols) or "(none)"
    lines.append(f"\nImport the target `{target.name}` and any helpers from `{target.module}`.")
    lines.append(f"Available symbols there: {symbols}")
    lines.append("Submit the test as `impl` (leave `tests` empty); checked by ruff + ty only.")
    return "\n".join(lines)


def _gate_section(gates: tuple[Gate, ...]) -> str | None:
    human = [gate.spec for gate in gates if gate.kind == "human"]
    if not human:
        return None
    return (
        "These author tests are part of the definition of done — your implementation will be run "
        "against them and must pass:\n" + "\n\n".join(human)
    )


def _class_section(context: ClassContext) -> str:
    attributes = "\n".join(f"  - self.{n}: {t or 'unknown'}" for n, t in context.attributes)
    methods = "\n".join(f"  - self.{n}{sig}" for n, sig in context.methods)
    return (
        f"This is a method of class {context.name}; the first parameter is the instance.\n"
        f"Instance attributes:\n{attributes or '  (none)'}\n"
        f"Sibling methods:\n{methods or '  (none)'}"
    )


def _test_instruction(declaration: Declaration) -> list[str]:
    if declaration.class_context is not None:
        cls = declaration.class_context.name
        return [
            f"Tests must `from {declaration.module} import {cls}`, build an instance, and call",
            f"the method on it: `obj = {cls}(...); obj.{declaration.name}(...)`.",
        ]
    return [
        f"Tests are named test_* functions that call `{declaration.name}` by its BARE name",
        "(it shares their namespace during validation; do not import it).",
    ]
