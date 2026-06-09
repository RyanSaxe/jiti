"""The `@jiti` decorator and its descriptor-safe wrapper.

The wrapper introspects the stub lazily on first call (so generated code can import back into
a fully-loaded module without cycles), then dispatches to the engine, which generates if
needed and returns the real implementation. It implements the descriptor protocol so methods
bind: `instance.method(...)` runs with the instance as the first argument.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import sys
import threading
import types
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, cast, overload

from jiti.agent.engine import Engine, default_engine
from jiti.core.declaration import (
    Declaration,
    Gate,
    UsedSymbol,
    analyze_body,
    gate_for,
    introspect,
    used_symbol,
)
from jiti.core.errors import JitiError
from jiti.core.validate import CONTRACT, run_test_callable


class _JitiCallable:
    """Lazily resolves a stub to its generated implementation and dispatches to it."""

    def __init__(
        self,
        func: Callable[..., Any],
        engine: Engine | None,
        uses: tuple[object, ...] = (),
    ) -> None:
        if not isinstance(func, types.FunctionType):
            raise JitiError("@jiti can only decorate plain functions and methods.")
        analyze_body(func)  # fail fast at decoration time if the body is a real implementation
        self._func: types.FunctionType = func
        self._is_async = inspect.iscoroutinefunction(func)
        self._engine = engine
        self._owner: type | None = None
        self._impl: Callable[..., Any] | None = None
        self._gates: list[Gate] = []
        self._uses: tuple[UsedSymbol, ...] = tuple(used_symbol(item) for item in uses)
        # Reentrant so a generation cascade that re-enters this target on the same thread
        # falls through to the engine's cycle guard instead of deadlocking.
        self._resolve_lock = threading.RLock()
        functools.update_wrapper(self, func)
        if self._is_async:
            inspect.markcoroutinefunction(self)

    def __set_name__(self, owner: type, name: str) -> None:
        self._owner = owner

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._is_async:
            return self._call_async(args, kwargs)
        return self._resolve(args, kwargs)(*args, **kwargs)

    async def _call_async(self, args: tuple[object, ...], kwargs: dict[str, object]) -> Any:
        impl = (
            await asyncio.to_thread(self._resolve, args, kwargs)
            if self._impl is None
            else self._impl
        )
        return await impl(*args, **kwargs)

    def __get__(self, instance: object, owner: type | None = None) -> Callable[..., Any]:
        # Class access (`Cls.method`) returns the wrapper unresolved — resolving here would
        # recurse, since introspecting the class to build its context reads its attributes.
        if instance is None:
            return self

        def bound(*args: Any, **kwargs: Any) -> Any:
            if self._is_async:
                return self._call_async((instance, *args), kwargs)
            return self._resolve((instance, *args), kwargs)(instance, *args, **kwargs)

        functools.update_wrapper(bound, self._func)  # carry __name__/__doc__/__qualname__
        if self._is_async:
            inspect.markcoroutinefunction(bound)
        return bound

    def declaration(self) -> Declaration:
        """The canonical spec for this stub — engine and CLI build state from it."""
        return introspect(self._func, self._owner, tuple(self._gates), uses=self._uses)

    def _resolve(self, args: tuple[object, ...], kwargs: dict[str, object]) -> Callable[..., Any]:
        # Double-checked lock: concurrent first calls (threads, or async tasks resolving via
        # to_thread) must not each run a generation — the losers wait, then use the winner's impl.
        if self._impl is None:
            with self._resolve_lock:
                if self._impl is None:
                    engine = self._engine or default_engine()
                    engine.discover()  # import test modules so gates are registered first
                    self._impl = CONTRACT(
                        engine.implement(self.declaration(), args, kwargs, target=self)
                    )
        return self._impl

    @contextmanager
    def routing_to(self, candidate: Callable[..., Any]) -> Iterator[None]:
        """Route every call to this target through `candidate` for the duration of the block.

        Used during validation: every gate test and every agent test that reaches this target
        — whether by bare name, `obj.method(...)`, or through a wrapper stack — short-circuits
        through `__call__` to `candidate` instead of trying to re-generate (which would cycle).
        The wrapper stack above the JitiCallable still runs normally; only the bottom of the
        stack diverts. Caches on wrappers that expose `cache_clear` (lru_cache, functools.cache,
        and anything following the same convention) are cleared first so per-args results from
        a previous validation cycle don't pollute the gate run.
        """
        previous = self._impl
        self._impl = candidate
        _clear_caches_above(self)
        try:
            yield
        finally:
            self._impl = previous


def _clear_caches_above(target: _JitiCallable) -> None:
    """Call `cache_clear()` on every layer of the wrapper stack that exposes it."""
    seen: set[int] = set()
    for wrapper in _wrapper_chain(target):
        if id(wrapper) in seen:
            return
        seen.add(id(wrapper))
        clear = getattr(wrapper, "cache_clear", None)
        if callable(clear):
            clear()
        if wrapper is target:
            return


def _wrapper_chain(target: _JitiCallable) -> Iterator[object]:
    """Yield the wrapper stack from the outermost primary binding down to `target`.

    For a module-level `@a @jiti def f`, the primary binding is `module.f` (which is `a(target)`).
    For `@staticmethod @jiti def m` on a class, the primary binding is the staticmethod on the
    class. Walks via `__wrapped__` (functools convention), then `__func__` (staticmethod/
    classmethod), then `fget` (property).
    """
    module = sys.modules.get(target._func.__module__)
    if module is None:
        return
    name = target._func.__name__
    qualname = target._func.__qualname__
    owner_path = qualname.rsplit(".", 1)[0] if "." in qualname else ""
    if owner_path and "<locals>" not in qualname:
        owner: object = module
        for part in owner_path.split("."):
            owner = getattr(owner, part, None)
            if owner is None:
                return
        if not isinstance(owner, type):
            return
        wrapper = owner.__dict__.get(name)
    else:
        wrapper = module.__dict__.get(name)
    while wrapper is not None:
        yield wrapper
        if wrapper is target:
            return
        wrapper = (
            getattr(wrapper, "__wrapped__", None)
            or getattr(wrapper, "__func__", None)
            or getattr(wrapper, "fget", None)
        )


class _Jiti:
    """The `@jiti` decorator as a callable object, so it can also host `jiti.required_for`."""

    @overload
    def __call__[F: Callable[..., Any]](self, func: F) -> F: ...

    @overload
    def __call__[F: Callable[..., Any]](
        self,
        *,
        engine: Engine | None = ...,
        uses: Sequence[Callable[..., Any] | type] = ...,
    ) -> Callable[[F], F]: ...

    def __call__[F: Callable[..., Any]](
        self,
        func: F | None = None,
        *,
        engine: Engine | None = None,
        uses: Sequence[Callable[..., Any] | type] = (),
    ) -> F | Callable[[F], F]:
        """Declare a function or method by its interface; jiti generates the implementation.

        Use bare (`@jiti`) for the default shared engine, or `@jiti(engine=...)` to supply your
        own (e.g. a custom Anthropic client or store). `uses=[...]` declares symbols the
        implementation MUST use — pass the callables/classes themselves; the agent is told
        their signatures and validation rejects a candidate that never references them.
        """
        if func is None:
            return lambda target: cast(F, _JitiCallable(target, engine, tuple(uses)))
        return cast(F, _JitiCallable(func, engine))

    def required_for[F: Callable[..., Any]](self, target: Callable[..., Any]) -> Callable[[F], F]:
        """Register a test as part of `target`'s definition of done — generation must satisfy it.

        Write it in your test file, importing the target. A real-bodied test is run as-is against
        the candidate; an empty-bodied stub is generated by jiti from `target`'s interface.
        """
        jiti_callable = _unwrap_to_jiti_callable(target)
        if jiti_callable is None:
            raise JitiError("jiti.required_for(target) needs a @jiti-decorated function or method.")

        def register(test: F) -> F:
            if not isinstance(test, types.FunctionType):
                raise JitiError("jiti.required_for can only decorate a plain test function.")
            gate = gate_for(test, jiti_callable)
            # idempotent: discovery may import a test file twice. Identity is (module,
            # qualname) so two same-named tests in different files don't silently collide
            # — they register as distinct gates.
            ident = _gate_identity(gate)
            if not any(_gate_identity(existing) == ident for existing in jiti_callable._gates):
                jiti_callable._gates.append(gate)
            if gate.kind == "human":
                return test
            return cast(F, _jiti_test_runner(test, jiti_callable))

        return register


def _gate_identity(gate: Gate) -> tuple[str, str]:
    """Stable identity for dedupe: (test module, test qualname). Stub gates have no test
    function (jiti-tests pre-generation), so fall back to (name, '') for those."""
    test = gate.test
    if test is None:
        return (gate.name, "")
    return (getattr(test, "__module__", ""), getattr(test, "__qualname__", gate.name))


def _unwrap_to_jiti_callable(target: object) -> _JitiCallable | None:
    """Find the `_JitiCallable` underneath descriptor and `functools.wraps`-style wrappers.

    Walks `__func__` first, then `__wrapped__`, then `fget`. Order matters: bound methods
    (what `getattr(Class, classmethod_name)` returns) proxy attribute access to `__func__`,
    so `bound_method.__wrapped__` tunnels through to `JitiCallable.__wrapped__` — the stub
    function — and overshoots. Checking `__func__` first lands directly on the JitiCallable.

    Handles the common stacks: `@staticmethod @jiti`, `@classmethod @jiti`, `@property @jiti`,
    `@lru_cache @jiti`, and any `functools.wraps`-style decorator. Returns `None` when no
    `_JitiCallable` is found.
    """
    seen: set[int] = set()
    while target is not None and id(target) not in seen:
        if isinstance(target, _JitiCallable):
            return target
        seen.add(id(target))
        target = (
            getattr(target, "__func__", None)
            or getattr(target, "__wrapped__", None)
            or getattr(target, "fget", None)
        )
    return None


def _jiti_test_runner(stub: types.FunctionType, target: _JitiCallable) -> Callable[..., Any]:
    """Wrap a jiti-test stub so running it (e.g. under pytest) generates and runs the real test."""

    @functools.wraps(stub)
    def run(*args: Any, **kwargs: Any) -> Any:
        engine = target._engine or default_engine()
        return run_test_callable(
            lambda: engine.run_test(introspect(stub), target.declaration())(*args, **kwargs)
        )

    return run


jiti = _Jiti()
