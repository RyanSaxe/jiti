"""The agent's own tests are committed under a prunable `test_scratch_*` naming convention."""

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore, scratch_rename


def test_scratch_rename_only_touches_unmarked_agent_tests():
    source = "def test_a():\n    pass\n\ndef test_scratch_b():\n    pass\n\ndef helper():\n    pass"
    renamed = scratch_rename(source)

    assert "def test_scratch_a():" in renamed
    assert "def test_scratch_b():" in renamed  # already scratch — left as-is, not double-prefixed
    assert "def test_scratch_scratch_" not in renamed
    assert "def helper():" in renamed  # non-tests untouched


def test_committed_agent_tests_are_scratch(tmp_path):
    impl = "def f(x: int) -> int:\n    return x * 2"
    agent_tests = "def test_doubles():\n    assert f(2) == 4"
    client = ScriptedClient([submit("f", impl, agent_tests, quality=9)])
    engine = Engine(client=client, store=JitiStore(tmp_path / ".jiti"))

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return double of x."""
        ...

    assert f(2) == 4

    committed = next((tmp_path / ".jiti" / "tests").rglob("test_*.py")).read_text()
    assert "def test_scratch_doubles" in committed
    assert "def test_doubles(" not in committed
