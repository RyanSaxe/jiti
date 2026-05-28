"""Prompt-guide resolution precedence and injection of style/test guidance into the prompt."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from jiti.agent.engine import Engine
from jiti.agent.prompts import STYLE_ENV, STYLE_FILE, _bundled, _resolve
from jiti.core.declaration import introspect
from jiti.core.errors import GenerationError
from jiti.core.store import JitiStore


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(STYLE_ENV, raising=False)


def test_bundled_default_is_returned_when_nothing_is_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert _resolve("style.md", STYLE_ENV, STYLE_FILE) == _bundled("style.md")
    assert _bundled("style.md").strip()


def test_project_file_overrides_the_bundled_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / STYLE_FILE).write_text("project house style")

    assert _resolve("style.md", STYLE_ENV, STYLE_FILE) == "project house style"


def test_env_var_path_wins_over_the_project_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / STYLE_FILE).write_text("project house style")
    override = tmp_path / "custom.md"
    override.write_text("env house style")
    monkeypatch.setenv(STYLE_ENV, str(override))

    assert _resolve("style.md", STYLE_ENV, STYLE_FILE) == "env house style"


def test_missing_env_var_path_raises(monkeypatch):
    monkeypatch.setenv(STYLE_ENV, "/no/such/style.md")

    with pytest.raises(FileNotFoundError):
        _resolve("style.md", STYLE_ENV, STYLE_FILE)


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


def _captured_system(*, style: str, test_guide: str) -> Any:
    client = CapturingClient()
    engine = Engine(
        client=client,
        store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"),
        style=style,
        test_guide=test_guide,
    )
    # The capturing client never submits, so generation fails — but only after the first
    # `messages.create`, by which point the system prompt is recorded.
    with pytest.raises(GenerationError):
        engine.implement(introspect(stub), ("hi",), {})
    return client.system


def test_style_and_test_guidance_are_injected_as_blocks():
    system = _captured_system(style="ZZZ-style-marker", test_guide="QQQ-test-marker")

    texts = "".join(block["text"] for block in system)
    assert "ZZZ-style-marker" in texts
    assert "QQQ-test-marker" in texts


def test_empty_guides_add_no_extra_blocks():
    assert len(_captured_system(style="  ", test_guide="")) == 1
