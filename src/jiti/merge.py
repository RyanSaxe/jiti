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
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from jiti.decorator import _JitiCallable
from jiti.discovery import import_file, module_name, walk_py_files
from jiti.errors import MergeError
from jiti.store import (
    Action,
    JitiStore,
    SectionRef,
    drop_section,
    inventory,
    module_relpath,
    parse_file,
)
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


def source_files(root: Path) -> dict[str, Path]:
    """Map each importable module in the project to its source file (excludes the mirror)."""
    mapping: dict[str, Path] = {}
    for path in walk_py_files(root):
        name, _ = module_name(path)
        mapping.setdefault(name, path)
    return mapping


def select(targets: Sequence[str], refs: Sequence[SectionRef]) -> list[SectionRef]:
    """Resolve user targets (file path, dotted module, or qualname) to the sections they name."""
    chosen: dict[str, SectionRef] = {}
    for target in targets:
        matches = _match(target, refs)
        if not matches:
            raise MergeError(f"no generated section matched '{target}' — try `jiti status`.")
        chosen.update((ref.key, ref) for ref in matches)
    return list(chosen.values())


def _match(target: str, refs: Sequence[SectionRef]) -> list[SectionRef]:
    if target.endswith(".py") or os.sep in target or Path(target).exists():
        module, _ = module_name(Path(target).resolve())
        return [ref for ref in refs if ref.module == module]
    exact = [ref for ref in refs if ref.key == target]
    if exact:
        return exact
    return [ref for ref in refs if ref.module == target or ref.key.startswith(f"{target}.")]


def run_merge(root: Path, targets: Sequence[str], merge_all: bool, dry_run: bool) -> int:
    """Fold the selected generated sections back into their source files. Returns an exit code.

    Gating runs in full before any write: introspecting a stub reads its source by line number,
    so rewriting one function would corrupt the next one's spec check. We resolve every section
    against the intact source first, then apply the (pure-text) rewrites.
    """
    mirror = root / ".jiti"
    refs = inventory(mirror)
    if not refs:
        print("nothing to merge (.jiti/ is empty or absent).")
        return 0

    chosen = list(refs) if merge_all else select(targets, refs)
    sources = source_files(root)
    store = JitiStore(mirror)

    plans: list[tuple[SectionRef, Path, Action]] = []
    blocked = False
    for ref in chosen:
        try:
            plans.append(_gate(ref, sources, store))
        except MergeError as error:
            print(f"skip {ref.key}: {error}")
            blocked = blocked or not merge_all

    for ref, source_path, action in plans:
        if not dry_run:
            _apply(ref, source_path, mirror)
        print(f"{'would merge' if dry_run else 'merged'} {ref.key}  ({action.value})")

    if not dry_run and plans:
        _remove_empty_dirs(mirror)
        if not _any_jiti_left(sources):
            print("no @jiti remains — you can drop jiti from your dependencies.")
    print(f"\n{'would merge' if dry_run else 'merged'} {len(plans)} section(s).")
    return 1 if blocked else 0


def _gate(
    ref: SectionRef, sources: dict[str, Path], store: JitiStore
) -> tuple[SectionRef, Path, Action]:
    if ref.is_method:
        raise MergeError("merging methods is not supported yet")
    source_path = sources.get(ref.module)
    if source_path is None:
        raise MergeError(f"source file for `{ref.module}` not found; regenerate or clear")
    action = _resolve_state(ref, source_path, store)
    if action in (Action.REGENERATE, Action.CONFLICT):
        raise MergeError("source and .jiti are out of sync — run it or your tests, then merge")
    return ref, source_path, action


def _apply(ref: SectionRef, source_path: Path, mirror: Path) -> None:
    imports, _ = parse_file(ref.impl_path.read_text())
    new_source = merge_into_source(
        source_path.read_text(), ref.name, ref.module, ref.section.body, imports
    )
    write_source(source_path, new_source)
    drop_section(ref.impl_path, ref.key)
    drop_section(_test_path(mirror, ref), ref.key)


def _resolve_state(ref: SectionRef, source_path: Path, store: JitiStore) -> Action:
    import_file(source_path)
    module = sys.modules.get(ref.module)
    wrapper = getattr(module, ref.name, None) if module is not None else None
    if not isinstance(wrapper, _JitiCallable):
        raise MergeError(f"no @jiti `{ref.qualname}` in {source_path.name}; regenerate or clear")
    return store.resolve(wrapper.declaration()).action


def _test_path(mirror: Path, ref: SectionRef) -> Path:
    relpath = module_relpath(ref.module)
    return mirror / "tests" / relpath.with_name(f"test_{relpath.name}")


def _remove_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


def _any_jiti_left(sources: dict[str, Path]) -> bool:
    return any("@jiti" in path.read_text() for path in set(sources.values()))


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
