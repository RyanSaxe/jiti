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
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from jiti.declaration import is_stub_node
from jiti.decorator import _JitiCallable
from jiti.discovery import import_file, import_test_modules, module_name, walk_py_files
from jiti.errors import MergeError
from jiti.store import (
    Action,
    JitiStore,
    Section,
    SectionRef,
    atomic_write,
    drop_section,
    inventory,
    parse_file,
    remove_empty_dirs,
    scratch_promote,
    test_path_for_module,
)
from jiti.validate import RUFF


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


def run_merge(
    root: Path,
    targets: Sequence[str],
    merge_all: bool,
    dry_run: bool,
    prune_scratch: bool = False,
) -> int:
    """Fold the selected generated sections back into their source files. Returns an exit code.

    Gating runs in full before any write: introspecting a stub reads its source by line number,
    so rewriting one function would corrupt the next one's spec check. We resolve every section
    against the intact source first, then apply the (pure-text) rewrites. After the impls land,
    test files are folded in: `@jiti.required_for` decorators are dropped from user tests (and
    stub bodies spliced in from the mirror), and agent scratch tests are appended to whichever
    user test file already references the impl — or ejected to a new file in the project's test
    layout. `--prune` drops the scratch tests instead of ejecting them.
    """
    mirror = root / ".jiti"
    refs = inventory(mirror)
    if not refs:
        print("nothing to merge (.jiti/ is empty or absent).")
        return 0

    chosen = list(refs) if merge_all else select(targets, refs)
    sources = source_files(root)
    store = JitiStore(mirror)
    # required_for gates contribute to each declaration's spec hash, so test modules must be
    # imported before the gate check below — otherwise the check sees a stale spec.
    import_test_modules([str(root)])

    plans: list[tuple[SectionRef, Path, Action]] = []
    blocked = False
    for ref in chosen:
        try:
            plans.append(_gate(ref, sources, store))
        except MergeError as error:
            print(f"skip {ref.key}: {error}")
            blocked = blocked or not merge_all

    # `_apply` strips @jiti from source, leaving the wrapper unreachable via import. Snapshot
    # each ref's gate locations now while the wrapper is still bound to the user's stub.
    gate_index: dict[str, list[_GateLocation]] = {
        ref.key: _gate_locations_for(ref) for ref, _, _ in plans
    }

    written: list[Path] = []
    for ref, source_path, action in plans:
        if not dry_run:
            written.append(_apply(ref, source_path))
        print(f"{'would merge' if dry_run else 'merged'} {ref.key}  ({action.value})")

    if not dry_run and plans:
        written.extend(_merge_test_files(root, mirror, plans, gate_index, prune_scratch))
        _ruff_batch(written)
        remove_empty_dirs(mirror)
        if not _any_jiti_left(sources):
            print("no @jiti remains — you can drop jiti from your dependencies.")
    print(f"\n{'would merge' if dry_run else 'merged'} {len(plans)} section(s).")
    return 1 if blocked else 0


def _gate(
    ref: SectionRef, sources: dict[str, Path], store: JitiStore
) -> tuple[SectionRef, Path, Action]:
    source_path = sources.get(ref.module)
    if source_path is None:
        raise MergeError(f"source file for `{ref.module}` not found; regenerate or clear")
    action = _resolve_state(ref, source_path, store)
    if action in (Action.REGENERATE, Action.CONFLICT):
        raise MergeError("source and .jiti are out of sync — run it or your tests, then merge")
    return ref, source_path, action


def _apply(ref: SectionRef, source_path: Path) -> Path:
    """Inline the section into source, drop the impl section. Returns `source_path` for ruff."""
    imports, _ = parse_file(ref.impl_path.read_text())
    new_source = merge_into_source(
        source_path.read_text(), ref.qualname, ref.module, ref.section.body, imports
    )
    write_source(source_path, new_source)
    drop_section(ref.impl_path, ref.key)
    return source_path


def _resolve_state(ref: SectionRef, source_path: Path, store: JitiStore) -> Action:
    import_file(source_path)
    target = _resolve_wrapper(ref.module, ref.qualname)
    if target is None:
        raise MergeError(f"no @jiti `{ref.qualname}` in {source_path.name}; regenerate or clear")
    return store.resolve(target.declaration()).action


def _resolve_wrapper(module: str, qualname: str) -> _JitiCallable | None:
    """Walk `module.qualname` through `sys.modules`; return the wrapper if it's still a stub."""
    target: object | None = sys.modules.get(module)
    for part in qualname.split("."):
        target = getattr(target, part, None)
        if target is None:
            return None
    return target if isinstance(target, _JitiCallable) else None


def _any_jiti_left(sources: dict[str, Path]) -> bool:
    return any("@jiti" in path.read_text() for path in set(sources.values()))


@dataclass(frozen=True)
class _GateLocation:
    """Where a gate's test function lives in source — captured before merging mutates anything."""

    test_path: Path
    test_name: str


def _gate_locations_for(ref: SectionRef) -> list[_GateLocation]:
    """Where each gate registered on `ref`'s wrapper lives in source. Empty if there are none."""
    target = _resolve_wrapper(ref.module, ref.qualname)
    if target is None:
        return []
    return [
        _GateLocation(
            test_path=Path(gate.test.__code__.co_filename).resolve(),
            test_name=gate.test.__name__,
        )
        for gate in target._gates
        if gate.test is not None
    ]


def _merge_test_files(
    root: Path,
    mirror: Path,
    plans: list[tuple[SectionRef, Path, Action]],
    gate_index: dict[str, list[_GateLocation]],
    prune_scratch: bool,
) -> list[Path]:
    """Fold mirror test sections back into source: drop @jiti.required_for decorators, splice
    generated stub bodies, and either eject agent scratch tests or drop them (--prune). Returns
    the list of files written (so `_ruff_batch` can format them in one pass)."""
    user_tests = _rewrite_required_for_tests(mirror, plans, gate_index)
    scratch = _handle_scratch_tests(root, mirror, plans, gate_index, prune_scratch)
    return user_tests + scratch


def _rewrite_required_for_tests(
    mirror: Path,
    plans: list[tuple[SectionRef, Path, Action]],
    gate_index: dict[str, list[_GateLocation]],
) -> list[Path]:
    by_test_file: dict[Path, set[str]] = defaultdict(set)
    for ref, _, _ in plans:
        for loc in gate_index[ref.key]:
            by_test_file[loc.test_path].add(loc.test_name)
    written: list[Path] = []
    for test_path, test_names in by_test_file.items():
        if (path := _rewrite_user_test_file(test_path, mirror, test_names)) is not None:
            written.append(path)
    return written


def _handle_scratch_tests(
    root: Path,
    mirror: Path,
    plans: list[tuple[SectionRef, Path, Action]],
    gate_index: dict[str, list[_GateLocation]],
    prune_scratch: bool,
) -> list[Path]:
    by_module: dict[str, list[SectionRef]] = defaultdict(list)
    for ref, _, _ in plans:
        by_module[ref.module].append(ref)
    written: list[Path] = []
    for module, refs in by_module.items():
        scratch_path = test_path_for_module(mirror, module)
        if not scratch_path.exists():
            continue
        if prune_scratch:
            for ref in refs:
                drop_section(scratch_path, ref.key)
        elif (path := _eject_module_scratch(scratch_path, refs, root, gate_index)) is not None:
            written.append(path)
    return written


def _rewrite_user_test_file(test_path: Path, mirror: Path, test_names: set[str]) -> Path | None:
    """Drop `@jiti.required_for` decorators on the named tests, splicing stub bodies from mirror.

    Returns `test_path` if it was rewritten; `None` if nothing matched."""
    test_module, _ = module_name(test_path)
    mirror_test = test_path_for_module(mirror, test_module)
    try:
        _, mirror_sections = parse_file(mirror_test.read_text())
    except FileNotFoundError:
        mirror_sections = {}

    source = test_path.read_text()
    tree = ast.parse(source)
    edits, spliced_keys = _plan_test_file_edits(tree, test_module, test_names, mirror_sections)
    if not edits:
        return None
    lines = source.splitlines()
    for start, end, replacement in sorted(edits, key=lambda e: -e[0]):
        lines = lines[: start - 1] + replacement + lines[end:]
    write_source(test_path, "\n".join(lines).rstrip("\n") + "\n")
    for key in spliced_keys:
        drop_section(mirror_test, key)
    return test_path


def _plan_test_file_edits(
    tree: ast.Module,
    test_module: str,
    test_names: set[str],
    mirror_sections: dict[str, Section],
) -> tuple[list[tuple[int, int, list[str]]], list[str]]:
    """Plan (start, end, replacement) edits for each gated test in `tree` matching `test_names`."""
    edits: list[tuple[int, int, list[str]]] = []
    spliced_keys: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name not in test_names:
            continue
        rf_decorators = [d for d in node.decorator_list if _is_required_for_decorator(d)]
        if not rf_decorators:
            continue
        section_key = f"{test_module}.{node.name}"
        section = mirror_sections.get(section_key)
        if is_stub_node(node) and section is not None:
            start = min(d.lineno for d in node.decorator_list)
            end = node.end_lineno or node.lineno
            edits.append((start, end, section.body.splitlines()))
            spliced_keys.append(section_key)
        else:
            for d in rf_decorators:
                edits.append((d.lineno, d.end_lineno or d.lineno, []))
    return edits, spliced_keys


def _eject_module_scratch(
    scratch_path: Path,
    refs: list[SectionRef],
    root: Path,
    gate_index: dict[str, list[_GateLocation]],
) -> Path | None:
    """Promote scratch tests for `refs` into a project test file (`test_scratch_*` →
    `test_*`), then drop the mirror sections. Imports from the mirror scratch file are merged
    into the destination's import block; defs whose names already exist in the destination are
    skipped to avoid silent overrides.

    Returns the destination path if anything was written; `None` if every promoted def would
    have collided (in which case the mirror sections are still dropped)."""
    scratch_imports, sections = parse_file(scratch_path.read_text())
    keys = [ref.key for ref in refs if ref.key in sections]
    if not keys:
        return None
    destination = _scratch_destination(refs, root, gate_index)
    block = _promoted_block([sections[key] for key in keys], destination)
    written = block is not None
    if written:
        new_text = _splice_promoted_block(destination, scratch_imports, block)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_source(destination, new_text)
    for key in keys:
        drop_section(scratch_path, key)
    return destination if written else None


def _promoted_block(sections: list[Section], destination: Path) -> str | None:
    """Build the begin/end-marked block of promoted scratch tests, skipping name collisions."""
    existing = _toplevel_def_names(destination.read_text()) if destination.exists() else set()
    bodies: list[str] = []
    for section in sections:
        body = _drop_conflicting_defs(scratch_promote(section.body), existing)
        if body.strip():
            bodies.append(body)
            existing.update(_toplevel_def_names(body))
    if not bodies:
        return None
    return f"{_PROMOTED_BEGIN}\n{'\n\n\n'.join(bodies)}\n{_PROMOTED_END}\n"


def _splice_promoted_block(destination: Path, scratch_imports: str, block: str) -> str:
    """Return the destination's full new text: existing content + injected imports + block."""
    if not destination.exists():
        preamble = (
            f"{_PROMOTED_HEADER}\n{scratch_imports}\n\n" if scratch_imports else _PROMOTED_HEADER
        )
        return f"{preamble}\n{block}"
    existing = destination.read_text()
    with_imports = _inject_imports(existing, scratch_imports) if scratch_imports else existing
    if not with_imports.endswith("\n"):
        with_imports += "\n"
    return f"{with_imports}\n\n{block}"


def _toplevel_def_names(source: str) -> set[str]:
    """Top-level FunctionDef / AsyncFunctionDef / ClassDef names in `source`."""
    return {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


def _drop_conflicting_defs(body: str, existing_names: set[str]) -> str:
    """Remove top-level defs from `body` whose names are already in `existing_names`."""
    tree = ast.parse(body)
    drop: set[int] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name not in existing_names:
            continue
        start = min((d.lineno for d in node.decorator_list), default=node.lineno)
        end = node.end_lineno or node.lineno
        drop.update(range(start, end + 1))
    if not drop:
        return body
    lines = body.splitlines()
    return "\n".join(line for i, line in enumerate(lines, 1) if i not in drop).strip("\n")


_PROMOTED_HEADER = (
    "# Agent-written tests, promoted by `jiti merge`.\n"
    "# Originally `test_scratch_*` from impl development — their prefix has been dropped so\n"
    "# pytest now runs them as real tests. Keep, edit, or delete as you would any other test.\n"
)
_PROMOTED_BEGIN = "# === jiti merge: promoted agent tests below ==="
_PROMOTED_END = "# === jiti merge: end promoted agent tests ==="


def _scratch_destination(
    refs: list[SectionRef],
    root: Path,
    gate_index: dict[str, list[_GateLocation]],
) -> Path:
    """Pick where to append scratch tests for an impl module: user test file if any gates it,
    else the test file convention discovered from the project's existing test layout."""
    gate_files: list[Path] = sorted(
        {loc.test_path for ref in refs for loc in gate_index.get(ref.key, [])}
    )
    if gate_files:
        return gate_files[0]
    impl_module = refs[0].module
    return _conventional_test_path(root, impl_module)


def _conventional_test_path(root: Path, impl_module: str) -> Path:
    """A fresh test-file path for `impl_module` that follows the project's existing layout.

    If any `tests/` directory exists near the impl (walking up from the impl's parent), place
    the new file inside it under a path that mirrors the impl. Otherwise create a `tests/`
    directory at the project root and place the file there.
    """
    relpath = Path(*impl_module.split("."))
    basename = f"test_{relpath.name}.py"
    for parent in [relpath.parent, *relpath.parent.parents]:
        candidate_dir = root / parent / "tests"
        if candidate_dir.is_dir():
            sub = relpath.relative_to(parent).parent
            return candidate_dir / sub / basename
    return root / "tests" / relpath.parent / basename


def _is_required_for_decorator(node: ast.expr) -> bool:
    """True for `@jiti.required_for(...)`."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "required_for"
        and isinstance(func.value, ast.Name)
        and func.value.id == "jiti"
    )


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


def _ruff_batch(paths: Sequence[Path]) -> None:
    """Run `ruff check --fix --select F401,I` then `ruff format` over `paths` in one pass each."""
    if not paths:
        return
    args = [str(p) for p in paths]
    subprocess.run(
        [*RUFF, "check", "--fix", "--select", "F401,I", "--quiet", *args], capture_output=True
    )
    subprocess.run([*RUFF, "format", "--quiet", *args], capture_output=True)
