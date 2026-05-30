"""Runtime contract enforcement: every call to a `@jiti` function passes through pydantic.

These tests pin that callers (including buggy tests) can't slip a wrongly typed argument
into a generated impl, and that an impl can't silently return the wrong type. The wrap
lives only while `@jiti` decorates the function — after `jiti merge` it's gone.

Note on `# ty: ignore[invalid-argument-type]`: several tests deliberately pass arguments
that violate the function's static type — that's the *point*. We suppress ty only there.
"""

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
    return Engine(client=ScriptedClient(list(responses)), store=JitiStore(tmp_path / ".jiti"))


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
_METHOD_ENGINE = Engine(client=_METHOD_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


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
