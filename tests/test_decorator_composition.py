"""`@jiti` composes with descriptor decorators (staticmethod, classmethod, property) and
function decorators (lru_cache). The body-only contract that prevents the agent from
wrapping the target itself is covered by `test_signature_drift.py`."""

import tempfile
from functools import lru_cache
from pathlib import Path

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore

# ---------- @staticmethod @jiti ----------


_STATIC_CLIENT = ScriptedClient([])
_STATIC_ENGINE = Engine(client=_STATIC_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


class _StaticHost:
    @staticmethod
    @jiti(engine=_STATIC_ENGINE)
    def doubled(x: int) -> int:
        """Return x * 2."""
        ...


def test_staticmethod_composes_with_jiti():
    body = "return x * 2"
    tests = "def test_s():\n    assert doubled(3) == 6"
    _STATIC_CLIENT.script = [submit("doubled", body, tests)]

    assert _StaticHost.doubled(5) == 10
    assert _STATIC_CLIENT.calls == 1


# ---------- @classmethod @jiti ----------


_CLASS_CLIENT = ScriptedClient([])
_CLASS_ENGINE = Engine(client=_CLASS_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


class _ClassHost:
    base = 10

    @classmethod
    @jiti(engine=_CLASS_ENGINE)
    def scaled(cls, n: int) -> int:
        """Return cls.base * n."""
        ...


def test_classmethod_composes_with_jiti():
    body = "return cls.base * n"
    tests = (
        "from test_decorator_composition import _ClassHost\n"
        "def test_c():\n    assert scaled(_ClassHost, 3) == 30"
    )
    _CLASS_CLIENT.script = [submit("scaled", body, tests)]

    assert _ClassHost.scaled(4) == 40
    assert _CLASS_CLIENT.calls == 1


# ---------- @property @jiti ----------


_PROP_CLIENT = ScriptedClient([])
_PROP_ENGINE = Engine(client=_PROP_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


class _PropHost:
    def __init__(self, value: int) -> None:
        self.value = value

    @property
    @jiti(engine=_PROP_ENGINE)
    def doubled(self) -> int:
        """Return self.value * 2."""
        ...


def test_property_composes_with_jiti():
    body = "return self.value * 2"
    tests = (
        "from test_decorator_composition import _PropHost\n"
        "def test_p():\n    assert doubled(_PropHost(7)) == 14"
    )
    _PROP_CLIENT.script = [submit("doubled", body, tests)]

    assert _PropHost(9).doubled == 18
    assert _PROP_CLIENT.calls == 1


# ---------- introspect derives the production call style ----------


def test_introspect_derives_call_style_for_each_descriptor_kind():
    """`call_style` is derived from the signature + the one `fget` check; the prompt and
    splice both read it without enumerating descriptor types."""
    from jiti.core.declaration import CallStyle

    assert _StaticHost.__dict__["doubled"].__func__.declaration().call_style is CallStyle.STATIC
    assert _ClassHost.__dict__["scaled"].__func__.declaration().call_style is CallStyle.METHOD
    prop_attr = _PropHost.__dict__["doubled"]
    assert prop_attr.fget is not None
    assert prop_attr.fget.declaration().call_style is CallStyle.ATTRIBUTE


def test_unwrap_finds_jiti_callable_through_classmethod_bound_method():
    """`Class.classmethod_name` returns a bound method whose `__wrapped__` tunnels through
    to the inner stub (overshooting). The unwrap must prefer `__func__` to land on the
    JitiCallable — otherwise merge skips stacked classmethods as "no @jiti found"."""
    from jiti.decorator import _JitiCallable, _unwrap_to_jiti_callable

    assert isinstance(_unwrap_to_jiti_callable(_ClassHost.scaled), _JitiCallable)
    assert isinstance(_unwrap_to_jiti_callable(_StaticHost.doubled), _JitiCallable)


# ---------- @lru_cache @jiti ----------


_CACHE_CLIENT = ScriptedClient([])
_CACHE_ENGINE = Engine(client=_CACHE_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


@lru_cache(maxsize=8)
@jiti(engine=_CACHE_ENGINE)
def cache_double(x: int) -> int:
    """Return x * 2."""
    ...


def test_lru_cache_composes_with_jiti():
    body = "return x * 2"
    tests = "def test_l():\n    assert cache_double(3) == 6"
    _CACHE_CLIENT.script = [submit("cache_double", body, tests)]

    assert cache_double(4) == 8
    assert cache_double(4) == 8  # cache hit — no re-resolution
    assert _CACHE_CLIENT.calls == 1
    # The cache is engaged: the repeat call shows up as a hit. (Miss counters from before
    # generation finished aren't preserved — routing_to clears caches between validation
    # cycles so per-args results from a discarded candidate can't leak forward.)
    assert cache_double.cache_info().hits >= 1
