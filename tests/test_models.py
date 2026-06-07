"""JITI_MODEL resolution for the default engine."""

from jiti.core.models import DEFAULT_MODEL, ENV_VAR, Model, resolve_default


def test_default_is_sonnet_when_env_unset(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_default() == DEFAULT_MODEL
    assert DEFAULT_MODEL is Model.SONNET_4_6


def test_env_var_picks_any_explicit_model(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "openai/gpt-5")
    assert resolve_default() == "openai/gpt-5"


def test_model_values_are_claude_ids():
    assert Model.OPUS_4_8.value == "claude-opus-4-8"
    assert Model.SONNET_4_6.value == "claude-sonnet-4-6"
    assert Model.HAIKU_4_5.value == "claude-haiku-4-5"
