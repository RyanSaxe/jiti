"""Splice a generated section into its source file.

The transform is text-preserving: it replaces the `@jiti` stub's exact line span with the
generated implementation (helpers + public function), keeps the user's signature line, and
brings along the impls' imports — minus self-imports of the module being merged into.
ruff is deferred to `_ruff_batch` so a multi-target merge formats every touched file in one
pass. Stripping `@jiti` also drops the runtime contract; post-merge code is plain Python.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from jiti.core.errors import MergeError
from jiti.core.store import atomic_write, drop_section, parse_file
from jiti.decorator import _JitiCallable, _unwrap_to_jiti_callable


def merge_into_source(
    source: str, qualname: str, own_module: str, section_body: str, file_imports: str
) -> str:
    """Return `source` with the `@jiti` stub `qualname` replaced by its generated implementation.

    `section_body` is the generated unit (private helpers + public function, no markers);
    `file_imports` is the companion's hoisted import block; `own_module` is the module being
    merged into, whose self-imports are dropped (the symbols already live in the file). The
    public function is spliced at the `@jiti` site (and re-indented if it's a class method);
    any module-level helpers in the section body (constants, private functions, etc.) are
    injected at module level near the imports — keeping them out of the class body where
    they'd cause syntax errors when stacked under decorators like `@staticmethod`.

    Non-`@jiti` decorators stacked above `@jiti` (e.g. `@staticmethod`, `@functools.cache`)
    are preserved on the merged def — only the `@jiti` decorator line itself is dropped.
    """
    node = _find_jiti_def_at(ast.parse(source), qualname)
    # _find_jiti_def_at already guarantees node has a @jiti decorator; this just locates it.
    jiti_decorator = next(d for d in node.decorator_list if _is_jiti_decorator(d))
    helpers, function = _split_section(section_body, node.name)
    spliced = _splice(source.splitlines(), node, function, jiti_decorator)
    if helpers.strip():
        spliced = _inject_module_block(spliced, helpers)
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
    """Walk `module.qualname` and unwrap to the underlying `_JitiCallable`, if any.

    Plain `@jiti` defs surface the JitiCallable directly via attribute access; stacked
    descriptors (`@staticmethod @jiti`, `@property @jiti`, etc.) surface their wrapper —
    `_unwrap_to_jiti_callable` peels through `__wrapped__` / `__func__` / `fget` to find
    the JitiCallable underneath. Same unwrap used by `jiti.required_for`.
    """
    target: object | None = sys.modules.get(module)
    for part in qualname.split("."):
        target = getattr(target, part, None)
        if target is None:
            return None
    return _unwrap_to_jiti_callable(target)


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


def _splice(
    lines: list[str],
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    function: str,
    jiti_decorator: ast.expr,
) -> str:
    """Replace the `@jiti` decorator + def body with `function`, preserving the user's signature.

    The user's signature is the contract — keeping it sidesteps Python forward-reference issues
    (a method body referring to its enclosing class by name) and any cosmetic drift the agent
    introduced (e.g. unquoted vs quoted annotations). Decorators above `@jiti` (e.g.
    `@staticmethod`) live at lower line numbers in source and are left untouched. `function`
    is the def-with-body only — helpers were already split out in `merge_into_source`.
    """
    start = jiti_decorator.lineno
    end = node.end_lineno or node.lineno
    rebuilt = _replace_signature(lines, node, function)
    function_lines = _reindent(rebuilt, node.col_offset).splitlines()
    return "\n".join(lines[: start - 1] + function_lines + lines[end:])


def _split_section(section_body: str, name: str) -> tuple[str, str]:
    """Split a section body into (module-level helpers, public function with its body).

    Helpers — any top-level constants, imports, or private defs in the section body — go to
    module level on merge. The public function (the one named `name`, with its body intact)
    stays at the splice site. If no public function with `name` is found, treat the whole
    body as the function and return no helpers.
    """
    public = _public_function(section_body, name)
    if public is None:
        return "", section_body
    section_lines = section_body.splitlines()
    helpers = "\n".join(section_lines[: public.lineno - 1]).strip("\n")
    function = "\n".join(section_lines[public.lineno - 1 :])
    return helpers, function


def _replace_signature(
    source_lines: list[str], user_node: ast.FunctionDef | ast.AsyncFunctionDef, function: str
) -> str:
    """Replace the def line(s) in `function` with `user_node`'s signature from source."""
    public = _public_function(function, user_node.name)
    if public is None:
        return function
    function_lines = function.splitlines()
    user_signature = _signature_lines(source_lines, user_node)
    public_sig_last = (public.body[0].lineno - 1) if public.body else public.lineno
    rebuilt = (
        function_lines[: public.lineno - 1] + user_signature + function_lines[public_sig_last:]
    )
    return "\n".join(rebuilt)


def _public_function(body: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The top-level def in `body` whose name matches `name` (the user's stub)."""
    for node in ast.parse(body).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _inject_module_block(source: str, block: str) -> str:
    """Inject `block` (module-level helpers) into `source` right after the existing imports.

    Uses the same anchor as `_inject_imports` so helpers and imports stay grouped at the top
    of the file. Ruff's batch pass at the end of merge will sort + dedupe everything.
    """
    if not block.strip():
        return source
    lines = source.splitlines()
    anchor = _import_anchor(ast.parse(source))
    block_lines = block.splitlines()
    # Add a blank-line separator below the block so it doesn't run into following code.
    if anchor < len(lines) and lines[anchor].strip():
        block_lines = [*block_lines, ""]
    return "\n".join(lines[:anchor] + block_lines + lines[anchor:])


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
    """An import is "self" only if it brings names already in scope post-merge.

    `from own_module import parse` is redundant once we're inside `own_module` — drop it.
    `from own_module import parse as _parse` introduces a NEW name (`_parse`); the merged
    code uses that alias, so the import must stay.
    """
    if isinstance(node, ast.ImportFrom):
        if node.level != 0 or node.module != own_module:
            return False
        return all(alias.asname is None for alias in node.names)
    if isinstance(node, ast.Import):
        return any(alias.name == own_module and alias.asname is None for alias in node.names)
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
