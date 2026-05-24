"""The `@jiti` decorator and its descriptor-safe wrapper.

The wrapper introspects the stub lazily (on first call, not at import — which keeps
generated code able to import back into a fully-loaded authored module without cycles).
It implements the descriptor protocol so methods bind correctly: `instance.method(...)`
dispatches to the generated implementation with the instance as the first argument.
"""

from __future__ import annotations

import functools
import types
from collections.abc import Callable
from typing import Any, cast, overload

from jiti.declaration import analyze_body, introspect
from jiti.errors import JitiError
from jiti.strategy import Strategy, default_strategy


class _JitiCallable:
    """Lazily resolves a stub to its generated implementation and dispatches to it."""

    def __init__(self, func: Callable[..., Any], strategy: Strategy | None) -> None:
        if not isinstance(func, types.FunctionType):
            raise JitiError("@jiti can only decorate plain functions and methods.")
        analyze_body(func)  # fail fast at decoration time if the body is a real implementation
        self._func: types.FunctionType = func
        self._strategy = strategy
        self._owner: type | None = None
        self._impl: Callable[..., Any] | None = None
        functools.update_wrapper(self, func)

    def __set_name__(self, owner: type, name: str) -> None:
        self._owner = owner

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __get__(self, instance: object, owner: type | None = None) -> Callable[..., Any]:
        impl = self._resolve()
        return impl if instance is None else functools.partial(impl, instance)

    def _resolve(self) -> Callable[..., Any]:
        if self._impl is None:
            strategy = self._strategy or default_strategy()
            self._impl = strategy.implement(introspect(self._func, self._owner))
        return self._impl


@overload
def jiti[F: Callable[..., Any]](func: F) -> F: ...


@overload
def jiti[F: Callable[..., Any]](*, strategy: Strategy | None = ...) -> Callable[[F], F]: ...


def jiti[F: Callable[..., Any]](
    func: F | None = None, *, strategy: Strategy | None = None
) -> F | Callable[[F], F]:
    """Declare a function or method by its interface; jiti writes the implementation.

    Use bare (`@jiti`) for the default code-generation strategy, or `@jiti(strategy=...)`
    to supply your own (e.g. a custom model or store).
    """
    if func is None:
        return lambda target: cast(F, _JitiCallable(target, strategy))
    return cast(F, _JitiCallable(func, strategy))
