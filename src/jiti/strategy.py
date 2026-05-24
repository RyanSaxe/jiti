"""Execution strategies: how a declaration becomes a runnable implementation.

`Codegen` is the MVP strategy — resolve the declaration against the store, generate and
commit if needed, then load the real code from the companion mirror. The `Strategy`
protocol leaves room for a future runtime-inference strategy (`jiti.live`).
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jiti.declaration import Declaration
from jiti.errors import ConflictError, JitiError
from jiti.generate import DEFAULT_MAX_ATTEMPTS, generate
from jiti.model import AnthropicModel, Model
from jiti.store import Action, JitiStore


class Strategy(Protocol):
    """Turn a declaration into a callable implementation."""

    def implement(self, declaration: Declaration) -> Callable[..., Any]: ...


@dataclass
class Codegen:
    """Generate real code once, commit it to the mirror, then run it forever after."""

    model_factory: Callable[[], Model]
    store: JitiStore
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def implement(self, declaration: Declaration) -> Callable[..., Any]:
        resolution = self.store.resolve(declaration)
        if resolution.action is Action.CONFLICT:
            raise ConflictError(
                f"{declaration.key}: the implementation was hand-edited and the declaration "
                "has since changed. Reconcile them before running."
            )
        if resolution.action in (Action.GENERATE, Action.REGENERATE):
            self._generate(declaration)
        return load_impl(self.store, declaration)

    def _generate(self, declaration: Declaration) -> None:
        if declaration.class_context is not None:
            raise JitiError(
                f"{declaration.key}: generating method implementations is not wired up yet "
                "(method dispatch works once an implementation exists)."
            )
        model = self.model_factory()
        with tempfile.TemporaryDirectory() as tmp:
            generated = generate(
                declaration,
                model,
                Path(tmp),
                import_path=_import_path(declaration),
                max_attempts=self.max_attempts,
            )
        committed_tests = (
            f"from {declaration.module} import {declaration.name}\n\n{generated.test_source}"
        )
        self.store.write(declaration, generated.impl_source, committed_tests)


def load_impl(store: JitiStore, declaration: Declaration) -> Callable[..., Any]:
    """Load the generated implementation by compiling its companion module fresh.

    Compiling the source directly (rather than importing the file) sidesteps Python's
    bytecode cache, so a just-edited companion always runs the code that's on disk.
    """
    path = store.impl_path(declaration)
    namespace: dict[str, Any] = {"__name__": f"_jiti.{declaration.module}", "__file__": str(path)}
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace[declaration.name]


def default_strategy() -> Strategy:
    return Codegen(model_factory=AnthropicModel, store=JitiStore(Path.cwd() / ".jiti"))


def _import_path(declaration: Declaration) -> tuple[str, ...]:
    """The path entry that makes the authored module importable, for import-backs."""
    module = sys.modules.get(declaration.module)
    file = getattr(module, "__file__", None)
    if file is None:
        return ()
    depth = len(declaration.module.split("."))
    return (str(Path(file).resolve().parents[depth - 1]),)
