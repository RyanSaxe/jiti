"""Model enum and JITI_MODEL env-var override for the default engine."""

import pytest

from jiti.core.errors import JitiError
from jiti.core.models import DEFAULT_MODEL, ENV_VAR, Model, resolve_default


def test_default_is_opus_when_env_unset(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_default() is DEFAULT_MODEL


def test_env_var_picks_a_cheaper_model(monkeypatch):
    monkeypatch.setenv(ENV_VAR, Model.SONNET_4_6.value)
    assert resolve_default() is Model.SONNET_4_6


def test_unknown_model_raises_with_valid_options(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "claude-banana-1")
    with pytest.raises(JitiError, match="claude-banana-1"):
        resolve_default()


def test_model_values_are_anthropic_ids():
    assert Model.OPUS_4_7.value == "claude-opus-4-7"
    assert Model.SONNET_4_6.value == "claude-sonnet-4-6"
    assert Model.HAIKU_4_5.value == "claude-haiku-4-5"
