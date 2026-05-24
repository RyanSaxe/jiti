"""Style-guide resolution precedence and its injection into the generation prompt."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jiti.declaration import introspect
from jiti.engine import Engine
from jiti.errors import GenerationError
from jiti.store import JitiStore
from jiti.style import STYLE_ENV, STYLE_FILENAME, default_style, resolve_style


@pytest.fixture(autouse=True)
def _no_env_style(monkeypatch):
    monkeypatch.delenv(STYLE_ENV, raising=False)


def test_bundled_default_is_returned_when_nothing_is_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_style() == default_style()
    assert default_style().strip()


def test_project_file_overrides_the_bundled_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / STYLE_FILENAME).write_text("project house style")

    assert resolve_style() == "project house style"


def test_env_var_path_wins_over_the_project_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / STYLE_FILENAME).write_text("project house style")
    override = tmp_path / "custom.md"
    override.write_text("env house style")
    monkeypatch.setenv(STYLE_ENV, str(override))

    assert resolve_style() == "env house style"


def test_missing_env_var_path_raises(monkeypatch):
    monkeypatch.setenv(STYLE_ENV, "/no/such/style.md")

    with pytest.raises(FileNotFoundError):
        resolve_style()


class CapturingClient:
    """Records the `system` argument of the first call, then returns a plain stop response."""

    def __init__(self) -> None:
        self.system: Any = None

    @property
    def messages(self) -> "CapturingClient":
        return self

    def create(self, **kwargs: Any) -> Any:
        self.system = kwargs["system"]

        class _Done:
            content = [type("Block", (), {"type": "text", "text": "done"})()]

        return _Done()


def stub(text: str) -> str:
    """Echo the text."""
    ...


def _captured_system(style: str) -> Any:
    client = CapturingClient()
    engine = Engine(client=client, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"), style=style)
    # The capturing client never submits, so generation fails — but only after the first
    # `messages.create`, by which point the system prompt is recorded.
    with pytest.raises(GenerationError):
        engine.implement(introspect(stub), ("hi",), {})
    return client.system


def test_style_is_injected_as_a_system_block():
    system = _captured_system("ZZZ-marker-style")

    texts = "".join(block["text"] for block in system)
    assert "ZZZ-marker-style" in texts


def test_empty_style_adds_no_second_block():
    assert len(_captured_system("   ")) == 1
