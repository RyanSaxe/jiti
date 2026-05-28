"""Fold test files when merging: drop `@jiti.required_for` decorators and eject scratch tests.

Two passes after the impls land in source: (1) for every gated test, drop its
`@jiti.required_for` decorator — splicing the generated stub body in from the mirror if the
user left the test as a stub; (2) for each impl module's mirror scratch file, either drop the
agent's scratch tests (`--prune`) or eject them to a real test file (the user test that gated
the impl, or a fresh path that mirrors the project's test layout).
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from jiti.cli.merge.source import _inject_imports, resolve_wrapper, write_source
from jiti.core.declaration import is_stub_node
from jiti.core.discovery import module_name
from jiti.core.store import (
    Action,
    Section,
    SectionRef,
    drop_section,
    parse_file,
    scratch_promote,
    test_path_for_module,
)


@dataclass(frozen=True)
class GateLocation:
    """Where a gate's test function lives in source — captured before merging mutates anything."""

    test_path: Path
    test_name: str


def gate_locations_for(ref: SectionRef) -> list[GateLocation]:
    """Where each gate registered on `ref`'s wrapper lives in source. Empty if there are none."""
    target = resolve_wrapper(ref.module, ref.qualname)
    if target is None:
        return []
    return [
        GateLocation(
            test_path=Path(gate.test.__code__.co_filename).resolve(),
            test_name=gate.test.__name__,
        )
        for gate in target._gates
        if gate.test is not None
    ]


def merge_test_files(
    root: Path,
    mirror: Path,
    plans: list[tuple[SectionRef, Path, Action]],
    gate_index: dict[str, list[GateLocation]],
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
    gate_index: dict[str, list[GateLocation]],
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
    gate_index: dict[str, list[GateLocation]],
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
    gate_index: dict[str, list[GateLocation]],
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
    gate_index: dict[str, list[GateLocation]],
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
