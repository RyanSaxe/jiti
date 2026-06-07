"""Async jiti targets: coroutine-shaped wrappers and awaited validation tests."""

import asyncio
import inspect
import tempfile
from pathlib import Path

from fakes import ScriptedClient, submit, submit_test

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore
from jiti.core.validate import validate


def test_validate_awaits_async_agent_tests():
    wrong = "async def async_double(x: int) -> int:\n    return x + 1"
    tests = "async def test_double() -> None:\n    assert await async_double(2) == 4"

    failing = validate(wrong, tests, name="async_double")
    passing = validate(
        "async def async_double(x: int) -> int:\n    return x * 2",
        tests,
        name="async_double",
    )

    assert not failing.ok
    assert "[tests]" in failing.report
    assert passing.ok


_SHAPE_CLIENT = ScriptedClient([])
_SHAPE_ENGINE = Engine(
    completion=_SHAPE_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti")
)


@jiti(engine=_SHAPE_ENGINE)
async def async_identity(value: int) -> int:
    """Return value."""
    ...


class AsyncBox:
    def __init__(self, value: int) -> None:
        self.value = value

    @jiti(engine=_SHAPE_ENGINE)
    async def identity(self) -> int:
        """Return self.value."""
        ...


def test_async_wrappers_are_seen_as_coroutine_functions():
    assert inspect.iscoroutinefunction(async_identity)
    assert inspect.iscoroutinefunction(AsyncBox(1).identity)


_GEN_CLIENT = ScriptedClient([])
_GEN_ENGINE = Engine(completion=_GEN_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@jiti(engine=_GEN_ENGINE)
async def generated_async_double(x: int) -> int:
    """Return x * 2."""
    ...


def test_async_function_generates_from_inside_a_running_event_loop():
    tests = "async def test_double() -> None:\n    assert await generated_async_double(2) == 4"
    _GEN_CLIENT.script = [submit("generated_async_double", "return x * 2", tests)]

    assert asyncio.run(generated_async_double(3)) == 6
    assert _GEN_CLIENT.calls == 1


_GATE_CLIENT = ScriptedClient([])
_GATE_ENGINE = Engine(completion=_GATE_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@jiti(engine=_GATE_ENGINE)
async def gated_async_double(x: int) -> int:
    """Return x * 2."""
    ...


@jiti.required_for(gated_async_double)
async def gate_async_double_uses_await() -> None:
    assert await gated_async_double(4) == 8


def test_async_human_gate_forces_a_retry():
    agent_tests = "async def test_double() -> None:\n    assert await gated_async_double(2) == 4"
    _GATE_CLIENT.script = [
        submit("gated_async_double", "return x + 2", agent_tests),
        submit("gated_async_double", "return x * 2", agent_tests),
    ]

    assert asyncio.run(gated_async_double(5)) == 10
    assert _GATE_CLIENT.calls == 2


_TDD_CLIENT = ScriptedClient([])
_TDD_ENGINE = Engine(completion=_TDD_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@jiti(engine=_TDD_ENGINE)
async def async_tdd_double(x: int) -> int:
    """Return x * 2."""
    ...


@jiti.required_for(async_tdd_double)
def gate_async_tdd_double() -> None:
    """async_tdd_double(2) == 4."""
    ...


def test_jiti_test_runner_awaits_generated_async_body():
    generated_gate = (
        f"from {__name__} import async_tdd_double\n\n"
        "async def gate_async_tdd_double() -> None:\n"
        "    assert await async_tdd_double(2) == 4"
    )
    agent_tests = "async def test_double() -> None:\n    assert await async_tdd_double(2) == 4"
    _TDD_CLIENT.script = [
        submit_test("gate", generated_gate),
        submit("async_tdd_double", "return x * 2", agent_tests),
    ]

    gate_async_tdd_double()

    assert asyncio.run(async_tdd_double(3)) == 6
    assert _TDD_CLIENT.calls == 2
