"""Body-only contract: jiti splices the stub's def line; agents write only the body.

The pre-body-only design needed a signature-drift comparator because agents could emit a
mismatched def line; now the def line is byte-identical to the stub's by construction, so
the tests here pin the splicing pipeline and the helper-contract guard rails instead.
"""

import inspect

import pytest

from jiti.core.declaration import (
    Declaration,
    extract_def_line,
    introspect,
    splice,
)
from jiti.core.errors import JitiError


def _decl(def_line: str, *, name: str = "run", module: str = "app.core") -> Declaration:
    return Declaration(
        module=module,
        qualname=name,
        name=name,
        signature=inspect.Signature(),
        docstring=None,
        hint=None,
        available_symbols=(),
        class_context=None,
        def_line=def_line,
    )


# ---------- extract_def_line ----------


def test_extract_def_line_single_line_signature():
    source = "def run(x: int) -> int: ...\n"

    assert extract_def_line(source, "run") == "def run(x: int) -> int:"


def test_extract_def_line_signature_split_across_lines():
    source = "def run(\n    x: int,\n    y: int,\n) -> int:\n    ...\n"

    assert extract_def_line(source, "run") == "def run(\n    x: int,\n    y: int,\n) -> int:"


def test_extract_def_line_strips_decorators():
    """`extract_def_line` returns the `def ... :` slice — never the decorators above it."""
    source = "@staticmethod\ndef run(x: int) -> int:\n    ...\n"

    assert extract_def_line(source, "run") == "def run(x: int) -> int:"


def test_introspect_populates_def_line_from_the_stub_source():
    def run(x: int, y: int) -> int:
        """Add x and y."""
        ...

    declaration = introspect(run)

    assert declaration.def_line == "def run(x: int, y: int) -> int:"


# ---------- splice ----------


def test_splice_concatenates_helpers_def_line_and_indented_body():
    declaration = _decl("def run(x: int) -> int:")

    result = splice(declaration, body="return x + 1", helpers="")

    assert result == "def run(x: int) -> int:\n    return x + 1\n"


def test_splice_normalizes_body_indentation():
    """A body the agent already indented (e.g. 8 spaces) is dedented and re-indented to 4."""
    declaration = _decl("def run(x: int) -> int:")

    result = splice(declaration, body="        return x + 1", helpers="")

    assert "    return x + 1" in result
    assert "        return x + 1" not in result


def test_splice_places_helpers_above_the_def_line():
    declaration = _decl("def run(x: int) -> int:")

    result = splice(
        declaration,
        body="return _double(x)",
        helpers="def _double(n: int) -> int:\n    return n * 2",
    )

    assert result.index("_double") < result.index("def run")


# ---------- helper contract ----------


def test_splice_rejects_public_helper_definitions():
    declaration = _decl("def run() -> int:")

    with pytest.raises(JitiError, match="PRIVATE"):
        splice(declaration, body="return helper()", helpers="def helper() -> int:\n    return 1")


def test_splice_rejects_a_helper_named_like_the_target():
    declaration = _decl("def run(x: int) -> int:", name="run")

    with pytest.raises(JitiError, match="redefine the target"):
        splice(declaration, body="return 1", helpers="def run(x: int) -> int:\n    return x")


def test_splice_allows_private_helpers_and_constants():
    declaration = _decl("def run(text: str) -> str:")

    result = splice(
        declaration,
        body="return _PATTERN.sub('-', text.lower())",
        helpers="import re\n\n_PATTERN = re.compile(r'\\s+')",
    )

    assert "_PATTERN" in result
    assert "import re" in result
