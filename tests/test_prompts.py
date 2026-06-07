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
    """Records the LiteLLM messages from the first call, then returns a plain stop response."""

    def __init__(self) -> None:
        self.messages: Any = None

    def __call__(self, **kwargs: Any) -> Any:
        self.messages = kwargs["messages"]
        message = type("Message", (), {"content": "done", "tool_calls": []})()
        choice = type("Choice", (), {"message": message})()
        return type("Done", (), {"choices": [choice], "usage": None})()


def stub(text: str) -> str:
    """Echo the text."""
    ...


def _captured_system(*, style: str, test_guide: str) -> Any:
    client = CapturingClient()
    engine = Engine(
        completion=client,
        store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"),
        style=style,
        test_guide=test_guide,
    )
    # The capturing client never submits, so generation fails — but only after the first
    # completion call, by which point the system prompt is recorded.
    with pytest.raises(GenerationError):
        engine.implement(introspect(stub), ("hi",), {})
    return _message_text(client.messages[0]["content"])


def _message_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n\n".join(str(block.get("text", "")) for block in content)
    return str(content)


def test_style_and_test_guidance_are_injected_as_blocks():
    system = _captured_system(style="ZZZ-style-marker", test_guide="QQQ-test-marker")

    assert "ZZZ-style-marker" in system
    assert "QQQ-test-marker" in system


def test_empty_guides_add_no_extra_blocks():
    system = _captured_system(style="  ", test_guide="")

    assert "Follow this house style" not in system
    assert "Follow this guidance when writing tests" not in system
