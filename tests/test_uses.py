"""`@jiti(uses=[...])`: the implementation must reference the declared symbols.

The contract is verified statically (a reference check over the candidate's AST), not by
runtime spying — references survive higher-order usage (`map(f, ...)`, `partial(f, ...)`)
where call-site matching is brittle, and they hold for every branch, not just the ones the
validation inputs happened to exercise.
"""

from pathlib import Path

import pytest
from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine, _task_prompt
from jiti.core.declaration import introspect, used_symbol
from jiti.core.errors import JitiError
from jiti.core.store import JitiStore
from jiti.core.validate import validate

HERE = str(Path(__file__).parent)


def is_even(n: int) -> bool:
    """True if n is even."""
    return n % 2 == 0


def twice(n: int) -> int:
    """Return n * 2."""
    return n * 2


TESTS = "def test_evens():\n    assert evens([1, 2, 3]) == [2]"


def test_missing_reference_fails_the_uses_check():
    inline = (
        "def evens(numbers: list[int]) -> list[int]:\n    return [n for n in numbers if n % 2 == 0]"
    )

    result = validate(inline, TESTS, name="evens", uses=(used_symbol(is_even),))

    assert not result.ok
    assert "[uses]" in result.report
    assert "is_even" in result.report


def test_direct_call_passes_the_uses_check():
    using = (
        "from test_uses import is_even\n\n"
        "def evens(numbers: list[int]) -> list[int]:\n"
        "    return [n for n in numbers if is_even(n)]"
    )

    result = validate(using, TESTS, name="evens", uses=(used_symbol(is_even),), import_path=[HERE])

    assert result.ok


def test_higher_order_reference_passes_where_call_matching_would_fail():
    """`map(twice, ...)` never produces a Call node on `twice` — the reference check
    accepts it anyway, which is the point of checking references over invocations."""
    higher_order = (
        "from test_uses import twice\n\n"
        "def doubled(numbers: list[int]) -> list[int]:\n"
        "    return list(map(twice, numbers))"
    )
    tests = "def test_d():\n    assert doubled([1, 2]) == [2, 4]"

    result = validate(
        higher_order, tests, name="doubled", uses=(used_symbol(twice),), import_path=[HERE]
    )

    assert result.ok


def test_aliased_import_counts_as_a_reference():
    aliased = (
        "from test_uses import twice as _double\n\n"
        "def doubled(numbers: list[int]) -> list[int]:\n"
        "    return [_double(n) for n in numbers]"
    )
    tests = "def test_d():\n    assert doubled([3]) == [6]"

    result = validate(
        aliased, tests, name="doubled", uses=(used_symbol(twice),), import_path=[HERE]
    )

    assert result.ok


def test_uses_rejects_plain_values():
    """`@jiti(uses=[3])` is already a static type error (the param takes callables and
    classes); `used_symbol` is the runtime boundary that backs the same rule for untyped
    callers."""
    with pytest.raises(JitiError, match="callables and classes"):
        used_symbol(3)


def test_uses_changes_the_spec_hash():
    def doubled(numbers: list[int]) -> list[int]:
        """Double every number."""
        ...

    without = introspect(doubled)
    with_uses = introspect(doubled, uses=(used_symbol(twice),))

    assert without.spec_hash != with_uses.spec_hash


def test_task_prompt_lists_must_use_symbols():
    def doubled(numbers: list[int]) -> list[int]:
        """Double every number."""
        ...

    declaration = introspect(doubled, uses=(used_symbol(twice),))
    prompt = _task_prompt(declaration)

    assert "MUST use" in prompt
    assert "twice(n: int) -> int" in prompt
    assert "Return n * 2." in prompt


def test_engine_rejects_candidates_until_the_symbol_is_used(tmp_path):
    tests = "def test_d():\n    assert double_all([1, 2]) == [2, 4]"
    client = ScriptedClient(
        [
            submit("double_all", "return [n * 2 for n in numbers]", tests),
            submit(
                "double_all",
                "return [twice(n) for n in numbers]",
                tests,
                helpers="from test_uses import twice",
            ),
        ]
    )
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))

    @jiti(engine=engine, uses=[twice])
    def double_all(numbers: list[int]) -> list[int]:
        """Double every number."""
        ...

    assert double_all([1, 2]) == [2, 4]
    assert client.calls == 2  # the inline first candidate failed the uses check


def test_uses_accepts_classes():
    class Money:
        """An amount in cents."""

        def __init__(self, cents: int) -> None:
            self.cents = cents

    symbol = used_symbol(Money)

    assert symbol.kind == "class"
    assert symbol.name == "Money"
    assert symbol.summary == "An amount in cents."
