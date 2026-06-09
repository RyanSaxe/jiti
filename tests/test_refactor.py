"""A low self-reported quality score earns one refactor pass before the impl is committed."""

import json
from pathlib import Path

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore

TESTS = "def test_f():\n    assert f(2) == 4"
ROUGH = "return x*2"
CLEAN = "return x * 2"


def _engine(client: ScriptedClient, tmp_path: Path, **kwargs) -> Engine:
    return Engine(completion=client, store=JitiStore(tmp_path / ".jiti"), **kwargs)


def test_low_quality_green_candidate_triggers_a_refactor_pass(tmp_path):
    # Both candidates pass the tests, so only the quality score can drive the extra turn.
    client = ScriptedClient(
        [submit("f", ROUGH, TESTS, quality=3), submit("f", CLEAN, TESTS, quality=9)]
    )
    engine = _engine(client, tmp_path, quality_threshold=7, max_refactor=1)

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return double of x."""
        ...

    assert f(3) == 6
    assert client.calls == 2  # the first green candidate was sent back for a quality refactor

    # The refactor turn must reach the provider wire-format clean: every message is
    # JSON-serializable, the submit's tool reply is linked by id, and the nudge is a
    # plain user message after it.
    refactor_turn = client.requests[1]["messages"]
    json.dumps(refactor_turn)
    tool_replies = [m for m in refactor_turn if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_replies] == ["t-f"]
    assert "quality 3 < 7" in refactor_turn[-1]["content"]
    assert refactor_turn[-1]["role"] == "user"


def test_refactor_is_capped_by_max_refactor(tmp_path):
    client = ScriptedClient(
        [submit("f", CLEAN, TESTS, quality=1), submit("f", CLEAN, TESTS, quality=1)]
    )
    engine = _engine(client, tmp_path, quality_threshold=7, max_refactor=1)

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return double of x."""
        ...

    assert f(3) == 6
    assert client.calls == 2  # one refactor pass, then committed despite the still-low score


def test_high_quality_commits_immediately(tmp_path):
    client = ScriptedClient([submit("f", CLEAN, TESTS, quality=9)])
    engine = _engine(client, tmp_path, quality_threshold=7)

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return double of x."""
        ...

    assert f(3) == 6
    assert client.calls == 1  # green and above threshold — no refactor, no wrap-up turn
