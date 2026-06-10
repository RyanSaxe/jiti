"""Process-global LLM activity heartbeat.

The execution timeout's question is not "how long has this run?" but "is legitimate work
still happening?" — and in jiti, legitimate long work is always an LLM call (a cascaded
generation inside a test, a slow model turn). Every LLM call marks itself here; the
timeout in `validate.call_bounded` only ticks while nothing is in flight, so a deep
cascade can run for hours while a `while True:` candidate trips quickly.

Process-global on purpose: cascades can cross engines (a callee bound to its own
`Engine`), and the only question that matters is whether *any* model call is running in
this process. Module-level functions over globals (not a singleton object) so call sites
read state at call time — easy to reset in tests, no stale-reference problems.
"""

from __future__ import annotations

import threading
from time import monotonic

_lock = threading.Lock()
_in_flight = 0
_last_activity = 0.0


def llm_call_started() -> None:
    global _in_flight, _last_activity
    with _lock:
        _in_flight += 1
        _last_activity = monotonic()


def llm_call_finished() -> None:
    global _in_flight, _last_activity
    with _lock:
        _in_flight = max(0, _in_flight - 1)
        _last_activity = monotonic()


def idle_for(since: float) -> float:
    """Seconds with no LLM activity, anchored at `since` (a `monotonic()` stamp).

    0.0 while any call is in flight. Otherwise, time since the later of `since` and the
    last call's completion — so a watcher that started after the last LLM call doesn't
    inherit idle time it never observed.
    """
    with _lock:
        if _in_flight > 0:
            return 0.0
        return monotonic() - max(_last_activity, since)


def reset() -> None:
    """Test hook: forget all activity."""
    global _in_flight, _last_activity
    with _lock:
        _in_flight = 0
        _last_activity = 0.0
