"""Frozen mode refuses to generate so a deploy can guarantee no LLM calls at runtime."""

import pytest
from fakes import ScriptedClient, submit

from jiti import FrozenError, jiti
from jiti.agent.engine import Engine, default_engine
from jiti.core.models import FROZEN_ENV_VAR, resolve_frozen
from jiti.core.store import JitiStore


def test_frozen_engine_raises_on_uncached_call(tmp_path):
    client = ScriptedClient([])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"), frozen=True)

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return x * 2."""
        ...

    with pytest.raises(FrozenError) as exc_info:
        f(3)

    assert client.calls == 0
    assert "frozen" in str(exc_info.value).lower()


def cached_stub(x: int) -> int:
    """Return x * 2."""
    ...


def test_frozen_engine_serves_cached_implementations(tmp_path):
    """A previously generated section runs as plain dispatch under freeze — that's the
    whole point: development warms the cache, production runs only what's committed.

    Both wrappers point at the same module-level stub so they share a qualname (and a
    section key); the frozen engine's resolution finds the warm engine's commit and
    serves it without calling the LLM."""
    store = JitiStore(tmp_path / ".jiti")
    warm_client = ScriptedClient(
        [submit("cached_stub", "return x * 2", "def test_f():\n    assert cached_stub(2) == 4")]
    )
    warm = jiti(engine=Engine(completion=warm_client, store=store))(cached_stub)
    assert warm(3) == 6
    assert warm_client.calls == 1

    cold_client = ScriptedClient([])
    cold = jiti(engine=Engine(completion=cold_client, store=store, frozen=True))(cached_stub)
    assert cold(5) == 10
    assert cold_client.calls == 0


def test_jiti_frozen_env_var_freezes_default_engine(monkeypatch):
    monkeypatch.setenv(FROZEN_ENV_VAR, "1")
    assert resolve_frozen() is True

    # default_engine() is built once per process; if a previous test has constructed it,
    # this assertion is about the env-resolver, not the singleton — exercise both directly.
    import jiti.agent.engine as engine_module

    monkeypatch.setattr(engine_module, "_DEFAULT", None)
    assert default_engine().frozen is True


def test_jiti_frozen_env_var_off_keeps_default_engine_unfrozen(monkeypatch):
    monkeypatch.delenv(FROZEN_ENV_VAR, raising=False)
    assert resolve_frozen() is False
