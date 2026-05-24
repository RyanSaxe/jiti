"""Prompt construction and the repair loop — jiti's generation engine.

From a `Declaration` we ask the model for a test module and an implementation module, then
validate (ruff + ty + pytest). On failure we feed the errors back and try again, up to a
bounded number of attempts. The function under test is referenced by bare name in the
tests; the real import is added later when the test section is committed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jiti.declaration import ClassContext, Declaration
from jiti.errors import GenerationError
from jiti.model import Model
from jiti.validate import validate

DEFAULT_MAX_ATTEMPTS = 4
_FENCE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Generated:
    impl_source: str
    test_source: str


def generate(
    declaration: Declaration,
    model: Model,
    workdir: Path,
    *,
    import_path: tuple[str, ...] = (),
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Generated:
    """Generate, validate, and repair an implementation until it passes or attempts run out."""
    prompt = _initial_prompt(declaration)
    report = ""
    for _ in range(max_attempts):
        impl, tests = parse_response(model.complete(prompt))
        test_module = _validation_module(declaration, tests)
        result = validate(impl, test_module, workdir, import_path=import_path)
        if result.ok:
            return Generated(impl_source=result.impl_source, test_source=tests)
        report = result.report
        prompt = _repair_prompt(declaration, impl, tests, report)
    raise GenerationError(
        f"{declaration.key}: validation still failing after {max_attempts} attempts:\n{report}"
    )


def parse_response(text: str) -> tuple[str, str]:
    return _extract_block(text, "IMPL"), _extract_block(text, "TESTS")


def _extract_block(text: str, label: str) -> str:
    marker = f"=== {label} ==="
    start = text.find(marker)
    if start == -1:
        raise GenerationError(f"model response is missing the `{marker}` section")
    fence = _FENCE.search(text, start)
    if fence is None:
        raise GenerationError(f"model response has no code fence after `{marker}`")
    return fence.group(1).strip()


def _validation_module(declaration: Declaration, test_source: str) -> str:
    """The test file run during validation: how the tests reach the candidate impl.

    For a method, import the authored class and patch the candidate onto it so the tests'
    `instance.method(...)` calls hit the candidate; for a function, a bare import suffices.
    """
    name = declaration.name
    if declaration.class_context is not None:
        cls = declaration.class_context.name
        return (
            f"from candidate import {name}\n"
            f"from {declaration.module} import {cls}\n"
            f"{cls}.{name} = {name}\n\n{test_source}"
        )
    return f"from candidate import {name}\n\n{test_source}"


def _initial_prompt(declaration: Declaration) -> str:
    return "\n".join(
        [
            "You are implementing one Python function from its interface. Write real,",
            "idiomatic, correct code — this is committed source, not a sketch.",
            "",
            _spec_section(declaration),
            "",
            _rules_section(declaration),
            "",
            _format_section(),
        ]
    )


def _repair_prompt(declaration: Declaration, impl: str, tests: str, report: str) -> str:
    return "\n".join(
        [
            "Your previous attempt failed validation. Fix it.",
            "",
            _spec_section(declaration),
            "",
            "Your previous implementation:",
            f"```python\n{impl}\n```",
            "",
            "Your previous tests:",
            f"```python\n{tests}\n```",
            "",
            "Validation errors (ruff / ty / pytest):",
            f"```\n{report}\n```",
            "",
            _rules_section(declaration),
            "",
            _format_section(),
        ]
    )


def _spec_section(declaration: Declaration) -> str:
    lines = [
        f"Function: {declaration.name}",
        f"Signature: def {declaration.name}{declaration.signature}",
    ]
    if declaration.docstring:
        lines.append(f"Description: {declaration.docstring}")
    if declaration.hint:
        lines.append(f"Implementation hint from the author:\n{declaration.hint}")
    if declaration.class_context:
        lines.append(_class_section(declaration.class_context))
    return "\n".join(lines)


def _class_section(context: ClassContext) -> str:
    attributes = "\n".join(
        f"  - self.{name}: {type_ or 'unknown'}" for name, type_ in context.attributes
    )
    methods = "\n".join(f"  - self.{name}{signature}" for name, signature in context.methods)
    return (
        f"This is a method of class {context.name}. The first parameter is the instance.\n"
        f"Available instance attributes:\n{attributes or '  (none)'}\n"
        f"Available sibling methods:\n{methods or '  (none)'}"
    )


def _rules_section(declaration: Declaration) -> str:
    imports = ", ".join(declaration.available_symbols) or "(none)"
    return "\n".join(
        [
            "Rules:",
            f"- Define exactly one public symbol: `{declaration.name}` with the signature above.",
            f"- Any helpers must be private and prefixed `_{declaration.name}__`.",
            f"- You may import the standard library and, from `{declaration.module}`, these "
            f"module-level names: {imports}.",
            *_test_rules(declaration),
            "- Cover the behavior thoroughly, including edge cases.",
        ]
    )


def _test_rules(declaration: Declaration) -> list[str]:
    if declaration.class_context is not None:
        cls = declaration.class_context.name
        return [
            f"- Implement as a top-level `def {declaration.name}(self, ...)`: instance first.",
            f"- Tests must construct a `{cls}` instance (in scope) and call the method on it,",
            f"  e.g. `obj = {cls}(...); obj.{declaration.name}(...)`.",
        ]
    return [
        "- Tests must be named pytest functions (test_*) that call the function by its bare",
        f"  name `{declaration.name}` (it is already in scope — do not import it).",
    ]


def _format_section() -> str:
    return "\n".join(
        [
            "Respond with EXACTLY these two sections and nothing else:",
            "=== IMPL ===",
            "```python",
            "<the implementation module>",
            "```",
            "=== TESTS ===",
            "```python",
            "<the test module>",
            "```",
        ]
    )
