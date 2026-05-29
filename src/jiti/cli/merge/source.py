"""Splice a generated section into its source file.

The transform is text-preserving: it replaces the `@jiti` stub's exact line span with the
generated implementation (helpers + public function), keeps the user's signature line, and
brings along the impls' imports — minus self-imports of the module being merged into.
ruff is deferred to `_ruff_batch` so a multi-target merge formats every touched file in one
pass.

Note: merge intentionally strips the `@jiti` decorator and, with it, the pydantic runtime
contract that wrapped every call (see `core.validate.CONTRACT`). Post-merge code is plain
Python with no jiti dependencies — type safety becomes the user's responsibility (their
type-checker, their tests). Do not restore the wrap here.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from jiti.core.errors import MergeError
from jiti.core.store import atomic_write, drop_section, parse_file
from jiti.decorator import _JitiCallable


def merge_into_source(
    source: str, qualname: str, own_module: str, section_body: str, file_imports: str
) -> str:
    """Return `source` with the `@jiti` stub `qualname` replaced by its generated implementation.

    `section_body` is the generated unit (helpers + public function, no markers); `file_imports`
    is the companion's hoisted import block; `own_module` is the module being merged into, whose
    self-imports are dropped (the symbols already live in the file). For a method qualname
    (`Class.method`), the splice descends into the class body and re-indents the section.
    """
    node = _find_jiti_def_at(ast.parse(source), qualname)
    # TODO: support @classmethod/@staticmethod stacking — needs the extra decorator captured in
    # Declaration and re-emitted here.
    if any(not _is_jiti_decorator(decorator) for decorator in node.decorator_list):
        raise MergeError(f"cannot merge `{qualname}`: it carries decorators other than @jiti.")
    spliced = _splice(source.splitlines(), node, section_body)
    needed = _strip_self_imports(file_imports, own_module)
    if needed:
        spliced = _inject_imports(spliced, needed)
    return spliced if spliced.endswith("\n") else spliced + "\n"


def write_source(path: Path, text: str) -> None:
    """Atomically replace `path`. Ruff is deferred to `_ruff_batch` at the end of `run_merge`,
    so a multi-target merge runs `ruff check` and `ruff format` exactly once across all files."""
    atomic_write(path, text if text.endswith("\n") else text + "\n")


def apply_section(ref, source_path: Path) -> Path:
    """Inline the section into source, drop the impl section. Returns `source_path` for ruff."""
    imports, _ = parse_file(ref.impl_path.read_text())
    new_source = merge_into_source(
        source_path.read_text(), ref.qualname, ref.module, ref.section.body, imports
    )
    write_source(source_path, new_source)
    drop_section(ref.impl_path, ref.key)
    return source_path


def resolve_wrapper(module: str, qualname: str) -> _JitiCallable | None:
    """Walk `module.qualname` through `sys.modules`; return the wrapper if it's still a stub."""
    target: object | None = sys.modules.get(module)
    for part in qualname.split("."):
        target = getattr(target, part, None)
        if target is None:
            return None
    return target if isinstance(target, _JitiCallable) else None


def _find_jiti_def_at(tree: ast.Module, qualname: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The single `@jiti`-decorated def named by `qualname`, descending through ClassDef parts."""
    parts = qualname.split(".")
    container: ast.Module | ast.ClassDef = tree
    for class_name in parts[:-1]:
        nested = next(
            (n for n in container.body if isinstance(n, ast.ClassDef) and n.name == class_name),
            None,
        )
        if nested is None:
            raise MergeError(f"no class `{class_name}` in source while merging `{qualname}`.")
        container = nested
    name = parts[-1]
    matches = [
        node
        for node in container.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == name
        and any(_is_jiti_decorator(decorator) for decorator in node.decorator_list)
    ]
    if not matches:
        raise MergeError(f"no @jiti-decorated `{qualname}` found to merge.")
    if len(matches) > 1:
        raise MergeError(
            f"found {len(matches)} @jiti `{qualname}` definitions; cannot merge safely."
        )
    return matches[0]


def _is_jiti_decorator(node: ast.expr) -> bool:
    """True for `@jiti` and `@jiti(...)` — peel the call/attribute to the leftmost name."""
    while isinstance(node, ast.Call):
        node = node.func
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "jiti"


def _splice(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef, body: str) -> str:
    """Replace the def's full span with `body`, preserving the user's signature line(s).

    The user's signature is the contract — keeping it sidesteps Python forward-reference issues
    (a method body referring to its enclosing class by name) and any cosmetic drift the agent
    introduced (e.g. unquoted vs quoted annotations).
    """
    start = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
    end = node.end_lineno or node.lineno
    rebuilt = _replace_signature_in_body(lines, node, body)
    body_lines = _reindent(rebuilt, node.col_offset).splitlines()
    return "\n".join(lines[: start - 1] + body_lines + lines[end:])


def _replace_signature_in_body(
    source_lines: list[str], user_node: ast.FunctionDef | ast.AsyncFunctionDef, body: str
) -> str:
    """Rewrite `body` so its public def uses `user_node`'s signature; leave helpers as-is."""
    section_lines = body.splitlines()
    public = _public_function(body, user_node.name)
    if public is None:
        return body
    user_signature = _signature_lines(source_lines, user_node)
    public_sig_first = public.lineno
    public_sig_last = (public.body[0].lineno - 1) if public.body else public.lineno
    rebuilt = (
        section_lines[: public_sig_first - 1] + user_signature + section_lines[public_sig_last:]
    )
    return "\n".join(rebuilt)


def _public_function(body: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The top-level def in `body` whose name matches `name` (the user's stub)."""
    for node in ast.parse(body).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _signature_lines(
    source_lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[str]:
    """The def's signature line(s) from source, dedented to col_offset 0."""
    first = node.lineno
    last = (node.body[0].lineno - 1) if node.body else node.lineno
    indent = " " * node.col_offset
    return [line.removeprefix(indent) for line in source_lines[first - 1 : last]]


def _reindent(body: str, col_offset: int) -> str:
    """Indent every non-empty line by `col_offset` spaces — for splicing into a class body."""
    if col_offset == 0:
        return body
    prefix = " " * col_offset
    return "\n".join(prefix + line if line else line for line in body.splitlines())


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
