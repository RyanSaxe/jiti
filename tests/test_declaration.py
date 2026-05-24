"""`declaration.py` turns a stub into jiti's canonical spec; these tests pin that down."""

from dataclasses import dataclass

import pytest

from jiti.declaration import BodyMode, class_context_of, introspect
from jiti.errors import RealBodyError

MARKER = object()


def stub_generate(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


def stub_hinted(text: str) -> str:
    """Convert text to a URL-safe slug."""
    # strip accents, lowercase, hyphenate runs of non-alphanumerics
    ...


def stub_pass() -> None:
    pass


def stub_raises(x: int) -> int:
    """Increment x."""
    raise NotImplementedError


def stub_with_real_body(x: int) -> int:
    return x + 1


def test_docstring_only_stub_generates_with_no_hint():
    declaration = introspect(stub_generate)

    assert declaration.body_mode is BodyMode.GENERATE
    assert declaration.hint is None


def test_comments_in_stub_become_a_generation_hint():
    declaration = introspect(stub_hinted)

    assert declaration.body_mode is BodyMode.HINT
    assert declaration.hint is not None
    assert "lowercase" in declaration.hint


def test_pass_body_is_a_stub():
    assert introspect(stub_pass).body_mode is BodyMode.GENERATE


def test_raise_not_implemented_is_a_stub():
    assert introspect(stub_raises).body_mode is BodyMode.GENERATE


def test_real_body_is_rejected():
    with pytest.raises(RealBodyError):
        introspect(stub_with_real_body)


def test_declaration_captures_interface():
    declaration = introspect(stub_generate)

    assert declaration.name == "stub_generate"
    assert declaration.key.endswith("stub_generate")
    assert str(declaration.signature) == "(text: str) -> str"
    assert declaration.docstring == "Convert text to a URL-safe slug."


def test_available_symbols_expose_module_level_names():
    declaration = introspect(stub_generate)

    assert "MARKER" in declaration.available_symbols
    assert all(not name.startswith("_") for name in declaration.available_symbols)


def test_spec_hash_is_stable_but_changes_with_the_spec():
    first = introspect(stub_generate)
    same = introspect(stub_generate)
    hinted = introspect(stub_hinted)

    assert first.spec_hash == same.spec_hash
    assert first.spec_hash != hinted.spec_hash


class Widget:
    count: int

    def __init__(self, name: str) -> None:
        self.name = name
        self.tags: list[str] = []

    def render(self) -> str: ...

    def parse(self, raw: str) -> int:
        """Parse raw into a count."""
        ...


def test_class_context_collects_attributes_and_sibling_methods():
    context = class_context_of(Widget, exclude="parse")

    attributes = dict(context.attributes)
    assert attributes["count"] == "int"
    assert attributes["name"] == ""
    assert attributes["tags"] == "list[str]"

    methods = dict(context.methods)
    assert "parse" not in methods
    assert "render" in methods
    assert "__init__" in methods


def test_method_declaration_includes_class_context():
    declaration = introspect(Widget.parse, owner=Widget)

    assert declaration.class_context is not None
    assert declaration.class_context.name == "Widget"
    assert declaration.qualname == "Widget.parse"


@dataclass
class Account:
    balance: int

    def withdraw(self, amount: int) -> int:
        """Withdraw amount from the balance."""
        ...


def test_class_context_handles_a_dataclass_init():
    context = introspect(Account.withdraw, owner=Account).class_context

    assert context is not None
    assert dict(context.attributes)["balance"] == "int"


def stub_forward(x: "list[str]") -> "list[str]":
    """Stringized (quoted) annotations."""
    ...


def test_signature_resolves_stringized_annotations():
    # The resolved signature is what the prompt shows (and what callers str), so quoted
    # forward-ref annotations come through as real types, not as `'list[str]'`.
    assert str(introspect(stub_forward).signature) == "(x: list[str]) -> list[str]"
