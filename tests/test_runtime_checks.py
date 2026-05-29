"""Runtime contract enforcement: every call to a `@jiti` function passes through pydantic.

These tests pin down the guarantee that callers (including buggy tests) can't slip a wrongly
typed argument into a generated impl, and that an impl can't silently return the wrong type.
The wrap lives only while `@jiti` decorates the function — after `jiti merge` it's gone.

Note on `# ty: ignore[invalid-argument-type]`: several tests deliberately pass arguments
that violate the function's static type. That's the *point* — the test verifies pydantic
catches what static analysis would also reject. We suppress ty only on those exact lines.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from fakes import Response, ScriptedClient, submit
from pydantic import ConfigDict, ValidationError, validate_call

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore

# Mirror of `decorator._validate_call` — these knobs are the contract we depend on, and the
# config tests below pin them independently of `decorator.py`. If the two ever drift, the
# integration tests further down (which use the real `@jiti` wrap) will catch the behavioral
# divergence.
_validate = validate_call(
    config=ConfigDict(strict=True, arbitrary_types_allowed=True),
    validate_return=True,
)


def _engine(tmp_path: Path, *responses: Response) -> Engine:
    return Engine(client=ScriptedClient(list(responses)), store=JitiStore(tmp_path / ".jiti"))


# ---------- the wrap's configuration ----------


def test_strict_mode_refuses_to_coerce_str_to_int():
    """strict=True — without it, '5' would be silently coerced to 5 and bugs slip through."""

    def takes_int(n: int) -> int:
        return n

    wrapped = _validate(takes_int)
    assert wrapped(5) == 5
    with pytest.raises(ValidationError):
        wrapped("5")  # ty: ignore[invalid-argument-type]


def test_validate_return_catches_a_lying_return_type():
    """validate_return=True — an impl that returns int despite `-> str` is flagged."""

    def lies(n: int) -> int:  # the lie: we'll cast this through Any to claim it returns str
        return n

    # Re-annotate at wrap time as `() -> str` so pydantic sees the mismatch.
    lies.__annotations__["return"] = str
    wrapped = _validate(lies)
    with pytest.raises(ValidationError):
        wrapped(1)


def test_arbitrary_types_allowed_via_isinstance():
    """arbitrary_types_allowed=True — non-pydantic classes (e.g. plain dataclasses) work."""

    @dataclass
    class Box:
        value: int

    def make_box(n: int) -> Box:
        return Box(n)

    wrapped = _validate(make_box)
    assert wrapped(7) == Box(7)
    with pytest.raises(ValidationError):
        wrapped("seven")  # ty: ignore[invalid-argument-type]


def test_unannotated_function_is_a_no_op():
    """No annotations → nothing to validate; the wrap must pass anything through."""

    def echo(x, y):  # noqa: ANN001
        return (x, y)

    wrapped = _validate(echo)
    assert wrapped(1, "two") == (1, "two")
    assert wrapped([1, 2], {"a": 1}) == ([1, 2], {"a": 1})


# ---------- integration: the wrap is actually applied by `@jiti` ----------


def slugify(text: str) -> str:
    """Lowercase and hyphenate."""
    ...


def test_bad_arg_type_to_a_jiti_function_raises_validation_error(tmp_path):
    impl = "def slugify(text: str) -> str:\n    return text.lower().replace(' ', '-')"
    tests = "def test_s():\n    assert slugify('Hi There') == 'hi-there'"
    engine = _engine(tmp_path, submit("slugify", impl, tests))

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
    impl = "def scaled(self, n: int) -> int:\n    return self.base * n"
    tests = (
        f"from {__name__} import Doubler\n\ndef test_s():\n    assert Doubler(3).scaled(4) == 12"
    )
    _METHOD_CLIENT.script = [submit("scaled", impl, tests)]

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
    impl = (
        f"from {__name__} import Version\n\n"
        "def parse_version(text: str) -> Version:\n    return Version(int(text))"
    )
    tests = (
        f"from {__name__} import Version\n\n"
        "def test_v():\n    assert parse_version('7') == Version(7)"
    )
    engine = _engine(tmp_path, submit("parse_version", impl, tests))
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
    impl = "def takes_int_jiti(n: int) -> int:\n    return n + 1"
    tests = "def test_t():\n    assert takes_int_jiti(2) == 3"
    engine = _engine(tmp_path, submit("takes_int_jiti", impl, tests))
    wrapped = jiti(engine=engine)(takes_int_jiti)

    assert wrapped(2) == 3
    with pytest.raises(ValidationError):
        wrapped("5")  # ty: ignore[invalid-argument-type]
