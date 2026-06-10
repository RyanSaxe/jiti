"""The minor-robustness batch: things that previously surfaced as ugly errors at the seams."""

from types import SimpleNamespace
from typing import Any

import pytest

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.agent.llm import LiteLLMClient, ToolCall, _json_object
from jiti.agent.tools import CallContext, dispatch
from jiti.core.declaration import introspect
from jiti.core.store import JitiStore


def _stub(x: int) -> int:
    """Return x * 2."""
    ...


def test_malformed_tool_arguments_become_a_parse_error_on_the_toolcall():
    """A truncated or malformed JSON payload should not raise out of `_content`. Instead
    the ToolCall carries a `parse_error` for the dispatcher to surface."""
    message = SimpleNamespace(
        content="",
        tool_calls=[
            SimpleNamespace(
                id="call-bad",
                function=SimpleNamespace(name="submit", arguments='{"body": "return x", '),
            )
        ],
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    client = LiteLLMClient(completion=lambda **_: response)

    result = client.complete(
        model="claude-sonnet-4-6", max_tokens=123, system=[], tools=[], messages=[]
    )

    assert len(result.content) == 1
    call = result.content[0]
    assert isinstance(call, ToolCall)
    assert call.parse_error is not None
    assert "valid JSON" in call.parse_error


def test_json_object_returns_error_for_non_object_payloads():
    parsed, error = _json_object('"just a string"')
    assert parsed == {}
    assert error is not None
    assert "JSON object" in error


def test_dispatch_surfaces_parse_errors_as_a_tool_failure():
    """The agent should see "FAILED" with the parse-error description on the next turn —
    NOT see the user's @jiti call raise."""
    context = CallContext(introspect(_stub), (3,), {})

    result = dispatch(context, "submit", {}, parse_error="bad JSON: missing comma")

    assert result.startswith("FAILED:")
    assert "bad JSON: missing comma" in result
    assert "Resend" in result


def test_bound_method_keeps_its_name_and_docstring():
    """`functools.update_wrapper` on the bound closure so framework introspection
    (and `repr`) shows the right name, not "bound"."""

    class Box:
        def __init__(self, x: int) -> None:
            self.x = x

        @jiti
        def doubled(self) -> int:
            """Return self.x doubled."""
            ...

    bound = Box(3).doubled
    assert bound.__name__ == "doubled"
    assert bound.__doc__ == "Return self.x doubled."


def test_gates_in_different_modules_with_the_same_name_coexist():
    """Two `test_target` functions in different modules registered on the same target
    should both register — not silently overwrite by name."""
    from types import FunctionType

    @jiti
    def target(x: int) -> int:
        """Return x * 2."""
        ...

    # Build two distinct function objects with the same __name__ but different __module__.
    def make_test(module: str) -> FunctionType:
        def test_target() -> None: ...

        test_target.__module__ = module
        return test_target

    jiti.required_for(target)(make_test("tests.test_a"))
    jiti.required_for(target)(make_test("tests.test_b"))

    # `target` is typed as the original function (the @jiti cast preserves the signature),
    # but at runtime it's the _JitiCallable wrapper that carries `_gates`.
    wrapper: Any = target
    assert len(wrapper._gates) == 2


def test_gates_idempotent_when_same_test_registers_twice():
    """The dedupe still works for a re-import of the same test function."""

    @jiti
    def target(x: int) -> int:
        """Return x * 2."""
        ...

    def test_target() -> None: ...

    jiti.required_for(target)(test_target)
    jiti.required_for(target)(test_target)

    wrapper: Any = target
    assert len(wrapper._gates) == 1


def test_import_path_for_init_module(tmp_path, monkeypatch):
    """A stub declared in `pkg/__init__.py` lives one level deeper on disk; the import
    path must back out one more so `import pkg` resolves."""
    import inspect as _inspect
    import sys as _sys

    from jiti.agent.engine import _import_path
    from jiti.core.declaration import Declaration

    pkg_dir = tmp_path / "init_pkg"
    pkg_dir.mkdir()
    init_file = pkg_dir / "__init__.py"
    init_file.write_text("")

    # Stand up a fake module entry pointing at the __init__.py.
    fake_module: Any = SimpleNamespace(__file__=str(init_file))
    monkeypatch.setitem(_sys.modules, "init_pkg", fake_module)

    declaration = Declaration(
        module="init_pkg",
        qualname="f",
        name="f",
        signature=_inspect.Signature(),
        docstring=None,
        hint=None,
        available_symbols=(),
        class_context=None,
        def_line="def f():",
    )
    paths = _import_path(declaration)
    assert paths == (str(tmp_path),)


def test_litellm_client_does_not_raise_on_malformed_tool_arguments():
    """End-to-end: a malformed payload all the way out of the LLM client returns a
    ToolCall carrying the parse error; the engine's dispatch will surface it."""
    message = SimpleNamespace(
        content="",
        tool_calls=[
            SimpleNamespace(
                id="x",
                function=SimpleNamespace(name="submit", arguments='{"body"'),
            )
        ],
    )
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
    client = LiteLLMClient(completion=lambda **_: response)

    result = client.complete(
        model="claude-sonnet-4-6", max_tokens=123, system=[], tools=[], messages=[]
    )

    [call] = result.content
    assert isinstance(call, ToolCall)
    assert call.parse_error is not None


def _dummy_completion(**_: Any) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[]))], usage=None
    )


@pytest.fixture
def _silent_engine(tmp_path):
    return Engine(completion=_dummy_completion, store=JitiStore(tmp_path / ".jiti"))
