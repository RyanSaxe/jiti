"""Import the project's test modules so `@jiti.required_for` gates register before generation.

A jiti function's gates are declared in your test files (`@jiti.required_for(target)`), which
register only when those files are imported. Under pytest, collection imports them; but a plain
call (your app, a demo) does not. So before generating, jiti imports them itself — on any entry
point. By default it scans the working tree for test files; configure `Engine(test_paths=...)`
to point at specific dirs/files (faster), or `Engine(test_paths=())` to turn it off.

Importing happens at generation time, when your code is already loaded, so a test's
`from your.module import target` resolves without a circular import. Like pytest collection,
this leaves the imported modules (and any test roots appended to `sys.path`) in place.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from jiti._log import logger

_SKIP = {
    ".jiti",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def import_test_modules(test_paths: Sequence[str] | None) -> None:
    """Import the test files under `test_paths` (or the whole tree if None) to register gates."""
    already: set[Path] = set()
    for module in list(sys.modules.values()):
        file = getattr(module, "__file__", None)
        if file:
            already.add(Path(file).resolve())
    for path in _test_files(test_paths):
        if path not in already:
            _import_file(path)


def _test_files(test_paths: Sequence[str] | None) -> list[Path]:
    roots = [Path.cwd()] if test_paths is None else [Path(path) for path in test_paths]
    found: set[Path] = set()
    for root in roots:
        if root.is_file():
            found.add(root.resolve())
        elif root.is_dir():
            found.update(_walk(root))
    return sorted(found)


def _walk(root: Path) -> set[Path]:
    # os.walk so we can prune skip-dirs in place — rglob would descend into .venv etc.
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP]
        found.update(Path(dirpath, name).resolve() for name in filenames if _is_test_file(name))
    return found


def _is_test_file(filename: str) -> bool:
    if not filename.endswith(".py"):
        return False
    return filename.startswith("test_") or filename.endswith("_test.py")


def _import_file(path: Path) -> None:
    # Import under the canonical dotted name (like pytest), so __module__, __file__, and the
    # generated companion paths are real — a synthetic name would corrupt import resolution.
    name, root = _module_name(path)
    if name in sys.modules:
        return
    if root not in sys.path:
        sys.path.append(root)  # append, not prepend, so discovery never shadows real packages
    try:
        importlib.import_module(name)
    except Exception as error:
        logger.warning("jiti: could not import %s — its gates won't apply (%s)", path, error)


def _module_name(path: Path) -> tuple[str, str]:
    """The file's dotted module name and the sys.path root it sits under (walks __init__.py)."""
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts)), str(parent)
