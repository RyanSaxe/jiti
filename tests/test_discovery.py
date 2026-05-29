"""Discovery imports your test modules so `@jiti.required_for` gates apply on any call path."""

import tempfile
from pathlib import Path

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.discovery import _test_files
from jiti.core.store import JitiStore

_CLIENT = ScriptedClient([])
_ENGINE = Engine(client=_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"), test_paths=())


@jiti(engine=_ENGINE)
def disco_target(x: int) -> int:
    """Return double of x."""
    ...


def test_test_files_finds_tests_and_prunes_skip_dirs(tmp_path):
    (tmp_path / "test_a.py").write_text("")
    (tmp_path / "b_test.py").write_text("")
    (tmp_path / "helper.py").write_text("")  # not a test
    skipped = tmp_path / ".venv"
    skipped.mkdir()
    (skipped / "test_inside_venv.py").write_text("")  # pruned

    found = {path.name for path in _test_files((str(tmp_path),))}

    assert found == {"test_a.py", "b_test.py"}


def test_a_gate_in_a_separate_file_is_discovered_and_enforced(tmp_path):
    # A gate the target's module never imports — only discovery brings it in.
    (tmp_path / "test_disco_gate.py").write_text(
        "from test_discovery import disco_target\n"
        "from jiti import jiti\n\n"
        "@jiti.required_for(disco_target)\n"
        "def gate_disco_doubles() -> None:\n"
        "    assert disco_target(4) == 8\n"
    )
    _ENGINE.test_paths = (str(tmp_path),)
    passes_agent_not_gate = (
        "def disco_target(x: int) -> int:\n    return x + 2"  # (2)==4 holds, (4)!=8 fails
    )
    correct = "def disco_target(x: int) -> int:\n    return x * 2"
    agent_tests = "def test_t():\n    assert disco_target(2) == 4"  # true for both candidates
    _CLIENT.script = [
        submit("disco_target", passes_agent_not_gate, agent_tests, quality=9),
        submit("disco_target", correct, agent_tests, quality=9),
    ]

    assert disco_target(3) == 6
    assert _CLIENT.calls == 2  # the discovered gate rejected the first candidate
