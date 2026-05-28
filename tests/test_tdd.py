"""A jiti-test (`@jiti.required_for` on an empty body) is generated from the target interface,
validated by ruff + ty only, and then gates the implementation's generation (red → green)."""

import tempfile
from pathlib import Path

from fakes import ScriptedClient, submit, submit_test

from jiti import jiti
from jiti.core.store import JitiStore
from jiti.engine import Engine

_CLIENT = ScriptedClient([])
_ENGINE = Engine(client=_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@jiti(engine=_ENGINE)
def tdd_double(x: int) -> int:
    """Return double of x."""
    ...


# A jiti-test stub: generated from tdd_double's interface, then run as a gate. Named without a
# `test_` prefix so pytest doesn't collect the runner; jiti runs it as a gate regardless.
@jiti.required_for(tdd_double)
def gate_tdd_double_doubles() -> None:
    """tdd_double(2) == 4 and tdd_double(5) == 10."""
    ...


def test_jiti_test_is_generated_then_gates_the_impl():
    jiti_test_body = (
        f"from {__name__} import tdd_double\n\n"
        "def gate_tdd_double_doubles():\n"
        "    assert tdd_double(2) == 4\n"
        "    assert tdd_double(5) == 10"
    )
    correct = "def tdd_double(x):\n    return x * 2"
    wrong = "def tdd_double(x):\n    return x + 2"  # passes its own test, fails the jiti gate
    agent_tests = "def test_d():\n    assert tdd_double(2) == 4"  # true for both impls
    _CLIENT.script = [
        submit_test("gate", jiti_test_body),  # test-mode generation (ruff + ty only)
        submit("tdd_double", wrong, agent_tests),  # rejected by the generated jiti gate
        submit("tdd_double", correct, agent_tests),  # satisfies it
    ]

    assert tdd_double(3) == 6
    # 1 = generate the jiti-test (test-mode), 2 = impl rejected by that gate, 3 = impl accepted.
    assert _CLIENT.calls == 3
