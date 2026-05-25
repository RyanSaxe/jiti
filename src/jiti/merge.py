"""Fold generated code from `.jiti/` back into your source — the heart of `jiti merge`.

Merging replaces a `@jiti` stub with the implementation jiti generated for it (the public
function plus its private helpers), drops the `@jiti` decorator, and brings along the imports
the implementation needs — minus self-imports of the module being merged into, which would be
circular. The transform is text-preserving: it splices the stub's exact line span and leaves
the rest of the file byte-for-byte; ruff tidies imports and formatting only on write.
"""

from __future__ import annotations

import ast
import os
import subprocess
import tempfile
from pathlib import Path

from jiti.errors import MergeError
from jiti.validate import RUFF


def merge_into_source(
    source: str, name: str, own_module: str, section_body: str, file_imports: str
) -> str:
    """Return `source` with the `@jiti` stub `name` replaced by its generated implementation.

    `section_body` is the generated unit (helpers + public function, no markers); `file_imports`
    is the companion's hoisted import block; `own_module` is the module being merged into, whose
    self-imports are dropped (the symbols already live in the file).
    """
    node = _find_jiti_def(ast.parse(source), name)
    if any(not _is_jiti_decorator(decorator) for decorator in node.decorator_list):
        raise MergeError(f"cannot merge `{name}`: it carries decorators other than @jiti.")
    spliced = _splice(source.splitlines(), node, section_body)
    needed = _strip_self_imports(file_imports, own_module)
    if needed:
        spliced = _inject_imports(spliced, needed)
    return spliced if spliced.endswith("\n") else spliced + "\n"


def write_source(path: Path, text: str) -> None:
    """Atomically replace a source file, then fix its imports and format it with ruff."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}.", suffix=".py")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_text(text if text.endswith("\n") else text + "\n")
        _ruff(tmp, "check", "--fix", "--select", "F401,I", "--quiet")
        _ruff(tmp, "format", "--quiet")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _find_jiti_def(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The single top-level `@jiti`-decorated def named `name` (ignores `@overload` siblings)."""
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
        and any(_is_jiti_decorator(decorator) for decorator in node.decorator_list)
    ]
    if not matches:
        raise MergeError(f"no @jiti-decorated `{name}` found to merge.")
    if len(matches) > 1:
        raise MergeError(f"found {len(matches)} @jiti `{name}` definitions; cannot merge safely.")
    return matches[0]


def _is_jiti_decorator(node: ast.expr) -> bool:
    """True for `@jiti` and `@jiti(...)` — peel the call/attribute to the leftmost name."""
    while isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "jiti"


def _splice(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef, body: str) -> str:
    """Replace the def's full span — decorators through last body line — with `body`."""
    start = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
    end = node.end_lineno or node.lineno
    return "\n".join(lines[: start - 1] + body.splitlines() + lines[end:])


def _strip_self_imports(imports: str, own_module: str) -> str:
    """Drop imports of `own_module` — its symbols already exist where we're merging into."""
    if not imports.strip():
        return ""
    lines = imports.splitlines()
    drop: set[int] = set()
    for node in ast.parse(imports).body:
        if _is_self_import(node, own_module):
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "\n".join(line for number, line in enumerate(lines, 1) if number not in drop).strip()


def _is_self_import(node: ast.stmt, own_module: str) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and node.module == own_module
    if isinstance(node, ast.Import):
        return any(alias.name == own_module for alias in node.names)
    return False


def _inject_imports(source: str, imports: str) -> str:
    lines = source.splitlines()
    anchor = _import_anchor(ast.parse(source))
    return "\n".join(lines[:anchor] + imports.splitlines() + lines[anchor:])


def _import_anchor(tree: ast.Module) -> int:
    """Line index to insert imports after: past existing imports, else the module docstring."""
    last_import = max(
        (
            node.end_lineno or node.lineno
            for node in tree.body
            if isinstance(node, ast.Import | ast.ImportFrom)
        ),
        default=0,
    )
    if last_import:
        return last_import
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.end_lineno or 1
    return 0


def _ruff(path: Path, *args: str) -> None:
    subprocess.run([*RUFF, *args, str(path)], capture_output=True)
