"""The `@jiti` decorator and its descriptor-safe wrapper.

The wrapper introspects the stub lazily on first call (so generated code can import back into
a fully-loaded module without cycles), then dispatches to the engine, which generates if
needed and returns the real implementation. It implements the descriptor protocol so methods
bind: `instance.method(...)` runs with the instance as the first argument.
"""

from __future__ import annotations

import functools
import types
from collections.abc import Callable
from typing import Any, cast, overload

from jiti.declaration import analyze_body, introspect
from jiti.engine import Engine, default_engine
from jiti.errors import JitiError


class _JitiCallable:
    """Lazily resolves a stub to its generated implementation and dispatches to it."""

    def __init__(self, func: Callable[..., Any], engine: Engine | None) -> None:
        if not isinstance(func, types.FunctionType):
            raise JitiError("@jiti can only decorate plain functions and methods.")
        analyze_body(func)  # fail fast at decoration time if the body is a real implementation
        self._func: types.FunctionType = func
        self._engine = engine
        self._owner: type | None = None
        self._impl: Callable[..., Any] | None = None
        functools.update_wrapper(self, func)

    def __set_name__(self, owner: type, name: str) -> None:
        self._owner = owner

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve(args, kwargs)(*args, **kwargs)

    def __get__(self, instance: object, owner: type | None = None) -> Callable[..., Any]:
        # Class access (`Cls.method`) returns the wrapper unresolved — resolving here would
        # recurse, since introspecting the class to build its context reads its attributes.
        if instance is None:
            return self

        def bound(*args: Any, **kwargs: Any) -> Any:
            return self._resolve((instance, *args), kwargs)(instance, *args, **kwargs)

        return bound

    def _resolve(self, args: tuple[object, ...], kwargs: dict[str, object]) -> Callable[..., Any]:
        if self._impl is None:
            engine = self._engine or default_engine()
            self._impl = engine.implement(introspect(self._func, self._owner), args, kwargs)
        return self._impl


class _Jiti:
    """The `@jiti` decorator as a callable object, so it can also host `jiti.required_for`."""

    @overload
    def __call__[F: Callable[..., Any]](self, func: F) -> F: ...

    @overload
    def __call__[F: Callable[..., Any]](
        self, *, engine: Engine | None = ...
    ) -> Callable[[F], F]: ...

    def __call__[F: Callable[..., Any]](
        self, func: F | None = None, *, engine: Engine | None = None
    ) -> F | Callable[[F], F]:
        """Declare a function or method by its interface; jiti generates the implementation.

        Use bare (`@jiti`) for the default shared engine, or `@jiti(engine=...)` to supply your
        own (e.g. a custom Anthropic client or store).
        """
        if func is None:
            return lambda target: cast(F, _JitiCallable(target, engine))
        return cast(F, _JitiCallable(func, engine))


jiti = _Jiti()
