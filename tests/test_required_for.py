"""`jiti.required_for` registers human gate tests that a target's generation must satisfy."""

import tempfile
from pathlib import Path

import pytest
from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.declaration import Gate, introspect
from jiti.engine import Engine
from jiti.errors import JitiError
from jiti.store import JitiStore

_CLIENT = ScriptedClient([])
_ENGINE = Engine(client=_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@jiti(engine=_ENGINE)
def gated_double(x: int) -> int:
    """Return double of x."""
    ...


# A gate, not a pytest test (no `test_` prefix), so only jiti runs it — against the candidate.
@jiti.required_for(gated_double)
def gate_double_is_doubling() -> None:
    assert gated_double(4) == 8


def test_human_gate_forces_a_retry_until_satisfied():
    passes_agent_but_not_gate = "def gated_double(x):\n    return x + 2"  # 2+2==4 but 4+2!=8
    correct = "def gated_double(x):\n    return x * 2"
    agent_tests = "def test_dbl():\n    assert gated_double(2) == 4"  # true for both impls
    _CLIENT.script = [
        submit("gated_double", passes_agent_but_not_gate, agent_tests),  # fails the gate
        submit("gated_double", correct, agent_tests),  # satisfies the gate
    ]

    assert gated_double(5) == 10
    assert _CLIENT.calls == 2  # the first candidate was rejected by the human gate


def plain(x: int) -> int:
    """Return double of x."""
    ...


def test_gates_are_part_of_the_spec_hash():
    base = introspect(plain)
    gate = Gate(name="g", kind="human", spec="assert plain(2) == 4")
    gated = introspect(plain, gates=(gate,))
    changed = Gate(name="g", kind="human", spec="assert plain(2) == 5")

    assert gated.spec_hash != base.spec_hash
    assert introspect(plain, gates=(changed,)).spec_hash != gated.spec_hash


def test_required_for_rejects_a_non_jiti_target():
    with pytest.raises(JitiError):
        jiti.required_for(plain)  # plain is not @jiti-decorated


class Box:
    @jiti(engine=_ENGINE)
    def grow(self, by: int) -> int:
        """Grow by `by`."""
        ...


def test_required_for_rejects_methods_for_now():
    with pytest.raises(NotImplementedError):
        jiti.required_for(Box.grow)
