"""The default Engine singleton must survive concurrent first callers as one instance."""

import threading

import jiti.agent.engine as engine_module
from jiti.agent.engine import Engine, default_engine


def test_default_engine_singleton_is_thread_safe(monkeypatch):
    """Without the lock, two threads racing through the `if _DEFAULT is None` check both
    construct an Engine; each holds its own `_in_progress` set, so the cycle guard breaks
    across them. The locked double-checked init must return one shared instance."""
    monkeypatch.setattr(engine_module, "_DEFAULT", None)

    instances: list[Engine] = []

    def take() -> None:
        instances.append(default_engine())

    threads = [threading.Thread(target=take) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first = instances[0]
    assert all(instance is first for instance in instances)


def test_default_engine_repeat_callers_get_the_same_instance(monkeypatch):
    """Sanity check: the second call returns the singleton built by the first."""
    monkeypatch.setattr(engine_module, "_DEFAULT", None)

    first = default_engine()
    second = default_engine()
    assert first is second
