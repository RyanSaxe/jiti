"""The LLM heartbeat: execution timeouts tick only while no model call is in flight.

Legitimate long work in jiti is always an LLM call (a cascaded generation inside a test,
a slow model turn), so the bounded runner measures IDLE time — a deep cascade can run for
hours while a `while True:` candidate trips quickly.
"""

import time
from time import monotonic

import pytest

from jiti.core import heartbeat
from jiti.core.validate import ExecutionTimeout, call_bounded


@pytest.fixture(autouse=True)
def _fresh_heartbeat():
    heartbeat.reset()
    yield
    heartbeat.reset()


def test_idle_is_zero_while_a_call_is_in_flight():
    heartbeat.llm_call_started()
    try:
        assert heartbeat.idle_for(since=monotonic() - 100) == 0.0
    finally:
        heartbeat.llm_call_finished()


def test_idle_is_anchored_at_the_watchers_start():
    """A watcher that starts after the last LLM call must not inherit idle time it never
    observed — idle counts from the later of its own start and the last activity."""
    assert heartbeat.idle_for(since=monotonic()) < 0.05


def test_llm_activity_keeps_a_slow_execution_alive():
    """Total runtime far exceeds the timeout, but calls keep landing — like a test that
    cascades through several generations. The bounded runner must not trip."""

    def slow_but_busy() -> str:
        for _ in range(6):
            heartbeat.llm_call_started()
            time.sleep(0.1)
            heartbeat.llm_call_finished()
        return "done"

    assert call_bounded(slow_but_busy, timeout=0.25, what="cascade") == "done"


def test_idle_execution_trips_after_the_timeout():
    def hang() -> None:
        time.sleep(5)

    with pytest.raises(ExecutionTimeout, match="no LLM activity"):
        call_bounded(hang, timeout=0.25, what="hang")
