"""Runtime contract enforcement: every call to a `@jiti` function passes through pydantic.

These tests pin that callers (including buggy tests) can't slip a wrongly typed argument
into a generated impl, and that an impl can't silently return the wrong type. The wrap
lives only while `@jiti` decorates the function — after `jiti merge` it's gone.

Note on `# ty: ignore[invalid-argument-type]`: several tests deliberately pass arguments
that violate the function's static type — that's the *point*. We suppress ty only there.
"""

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from fakes import Response, ScriptedClient, submit
from pydantic import ValidationError

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore


def _engine(tmp_path: Path, *responses: Response) -> Engine:
    return Engine(completion=ScriptedClient(list(responses)), store=JitiStore(tmp_path / ".jiti"))


def slugify(text: str) -> str:
    """Lowercase and hyphenate."""
    ...


def test_bad_arg_type_to_a_jiti_function_raises_validation_error(tmp_path):
    body = "return text.lower().replace(' ', '-')"
    tests = "def test_s():\n    assert slugify('Hi There') == 'hi-there'"
    engine = _engine(tmp_path, submit("slugify", body, tests))

    wrapped = jiti(engine=engine)(slugify)

    assert wrapped("Hi There") == "hi-there"
    with pytest.raises(ValidationError):
        wrapped(123)  # ty: ignore[invalid-argument-type]


# ---------- integration: method binding routes through the wrap ----------


_METHOD_CLIENT = ScriptedClient([])
_METHOD_ENGINE = Engine(
    completion=_METHOD_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti")
)


class Doubler:
    def __init__(self, base: int) -> None:
        self.base = base

    @jiti(engine=_METHOD_ENGINE)
    def scaled(self, n: int) -> int:
        """Return base * n."""
        ...


def test_method_violation_reaches_pydantic_through_get_binding():
    body = "return self.base * n"
    tests = (
        f"from {__name__} import Doubler\n\ndef test_s():\n    assert Doubler(3).scaled(4) == 12"
    )
    _METHOD_CLIENT.script = [submit("scaled", body, tests)]

    assert Doubler(3).scaled(4) == 12  # warms the cache; uses __get__
    with pytest.raises(ValidationError):
        Doubler(3).scaled("nope")  # ty: ignore[invalid-argument-type]


# ---------- integration: arbitrary dataclass types end-to-end ----------


@dataclass(frozen=True)
class Version:
    major: int


def parse_version(text: str) -> Version:
    """Parse 'N' into a Version."""
    ...


def test_arbitrary_dataclass_return_is_accepted_end_to_end(tmp_path):
    body = "return Version(int(text))"
    helpers = f"from {__name__} import Version"
    tests = (
        f"from {__name__} import Version\n\n"
        "def test_v():\n    assert parse_version('7') == Version(7)"
    )
    engine = _engine(tmp_path, submit("parse_version", body, tests, helpers=helpers))
    wrapped = jiti(engine=engine)(parse_version)

    assert wrapped("7") == Version(7)
    with pytest.raises(ValidationError):
        wrapped(7)  # ty: ignore[invalid-argument-type]


# ---------- integration: strict mode applies end-to-end ----------


def takes_int_jiti(n: int) -> int:
    """Return n + 1."""
    ...


def test_strict_mode_is_in_effect_on_jiti_wrapped_functions(tmp_path):
    """If decorator.py ever drops strict=True, calling with '5' would silently succeed."""
    body = "return n + 1"
    tests = "def test_t():\n    assert takes_int_jiti(2) == 3"
    engine = _engine(tmp_path, submit("takes_int_jiti", body, tests))
    wrapped = jiti(engine=engine)(takes_int_jiti)

    assert wrapped(2) == 3
    with pytest.raises(ValidationError):
        wrapped("5")  # ty: ignore[invalid-argument-type]


# ---------- pre-generation: contract-violating args never reach the agent ----------


def test_bad_arg_type_fails_before_any_generation(tmp_path):
    """An empty script proves it: any LLM call would IndexError, and calls stays 0."""
    client = ScriptedClient([])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))
    wrapped = jiti(engine=engine)(slugify)

    with pytest.raises(ValidationError):
        wrapped(123)  # ty: ignore[invalid-argument-type]
    assert client.calls == 0


def test_bad_arity_fails_before_any_generation(tmp_path):
    client = ScriptedClient([])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))
    wrapped = jiti(engine=engine)(slugify)

    with pytest.raises(ValidationError):
        wrapped()  # ty: ignore[missing-argument]
    assert client.calls == 0


def raises_form_stub(text: str) -> str:
    """Uppercase the text."""
    raise NotImplementedError


def test_raise_not_implemented_stub_form_still_generates(tmp_path):
    body = "return text.upper()"
    tests = "def test_u():\n    assert raises_form_stub('hi') == 'HI'"
    engine = _engine(tmp_path, submit("raises_form_stub", body, tests))
    wrapped = jiti(engine=engine)(raises_form_stub)

    assert wrapped("hi") == "HI"


def test_async_bad_args_fail_before_any_generation(tmp_path):
    client = ScriptedClient([])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))

    @jiti(engine=engine)
    async def double(x: int) -> int:
        """Return x * 2."""
        ...

    with pytest.raises(ValidationError):
        asyncio.run(double("bad"))  # ty: ignore[invalid-argument-type]
    assert client.calls == 0


# ---------- cascade: a bad call site gets contract feedback, not a polluted callee ----------


_CASCADE_CLIENT = ScriptedClient([])
_CASCADE_ENGINE = Engine(
    completion=_CASCADE_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti")
)


@jiti(engine=_CASCADE_ENGINE)
def cascade_callee(text: str) -> str:
    """Echo the text."""
    ...


@jiti(engine=_CASCADE_ENGINE)
def cascade_upstream(value: int) -> str:
    """Render the value via cascade_callee."""
    ...


def test_cascading_caller_gets_validation_feedback_without_generating_the_callee():
    """The upstream's first candidate calls the callee with a contract-violating arg
    (typed `Any` so it slips past ty and only the runtime contract can catch it). The
    callee must not generate from that call; the upstream agent must see the
    ValidationError as feedback and correct its call site."""
    helpers = f"from typing import Any\n\nfrom {__name__} import cascade_callee"
    bad = "data: Any = 123\nreturn cascade_callee(data)"
    good = "return cascade_callee(str(value))"
    tests = "def test_u():\n    assert cascade_upstream(7) == '7'"
    callee_tests = "def test_e():\n    assert cascade_callee('a') == 'a'"
    _CASCADE_CLIENT.script = [
        submit("cascade_upstream", bad, tests, helpers=helpers),
        submit("cascade_upstream", good, tests, helpers=f"from {__name__} import cascade_callee"),
        submit("cascade_callee", "return text", callee_tests),
    ]

    assert cascade_upstream(7) == "7"
    assert _CASCADE_CLIENT.calls == 3
    assert "ValidationError" in str(_CASCADE_CLIENT.requests[1])
