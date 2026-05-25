"""The `jiti` command-line tool.

Commands operate on the `.jiti/` mirror: `status` inspects it (read-only, never imports your
code), `merge` folds generated code back into your source, `test prune`/`keep` manage the
generated jiti-tests, and `clear` deletes the mirror.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from jiti.discovery import walk_py_files
from jiti.merge import source_files
from jiti.store import (
    JitiStore,
    Section,
    SectionRef,
    content_hash,
    inventory,
    module_relpath,
    parse_file,
    parse_sections,
    remove_empty_dirs,
    save_sections,
)


def status(root: Path) -> int:
    """Report every generated section and its state. Disk-only — imports nothing."""
    mirror = root / ".jiti"
    refs = inventory(mirror)
    if not refs:
        print("no generated code (.jiti/ not found)" if not mirror.exists() else "no sections.")
        return 0

    sources = source_files(root)
    by_module: dict[str, list[SectionRef]] = defaultdict(list)
    for ref in refs:
        by_module[ref.module].append(ref)

    edited = methods = kept_total = scratch_total = 0
    for module in sorted(by_module):
        print(_display_path(sources.get(module), module, root))
        for ref in sorted(by_module[module], key=lambda ref: ref.qualname):
            kept, scratch = _test_counts(mirror, ref)
            kept_total, scratch_total = kept_total + kept, scratch_total + scratch
            edited += ref.section.edited
            methods += ref.is_method
            note = "  (merge: not supported yet)" if ref.is_method else ""
            state = "method" if ref.is_method else ("edited" if ref.section.edited else "clean")
            print(f"  {ref.qualname:<24} {state:<7} tests: {kept} kept, {scratch} scratch{note}")
        print()

    print(
        f"{len(refs)} section(s) in {len(by_module)} file(s) · {edited} edited · "
        f"{methods} method(s) · {kept_total} kept, {scratch_total} scratch tests"
    )
    return 0


def _test_counts(mirror: Path, ref: SectionRef) -> tuple[int, int]:
    """`(kept, scratch)` test counts for a section, read from its `.jiti/tests/` companion."""
    relpath = module_relpath(ref.module)
    test_path = mirror / "tests" / relpath.with_name(f"test_{relpath.name}")
    try:
        section = parse_sections(test_path.read_text()).get(ref.key)
    except FileNotFoundError:
        return 0, 0
    if section is None:
        return 0, 0
    scratch = len(re.findall(r"^def test_scratch_", section.body, re.MULTILINE))
    total = len(re.findall(r"^def test_", section.body, re.MULTILINE))
    return total - scratch, scratch


def _display_path(source: Path | None, module: str, root: Path) -> str:
    if source is None:
        return f"{module}  (source not found)"
    try:
        return str(source.relative_to(root))
    except ValueError:
        return str(source)


def prune(root: Path, dry_run: bool) -> int:
    """Delete agent-written scratch tests (`test_scratch_*`) from the mirror."""
    tests_dir = root / ".jiti" / "tests"
    if not tests_dir.exists():
        print("no generated tests (.jiti/tests/ not found).")
        return 0

    removed = 0
    for path in sorted(walk_py_files(tests_dir)):
        imports, sections = parse_file(path.read_text())
        kept: dict[str, Section] = {}
        for key, section in sections.items():
            removed += len(_scratch_names(section.body))
            body = _drop_scratch(section.body)
            if body.strip():
                kept[key] = Section(key, section.spec_hash, content_hash(body), body)
        if not dry_run:
            save_sections(path, imports, kept)

    if not dry_run:
        remove_empty_dirs(root / ".jiti")
    print(f"{'would prune' if dry_run else 'pruned'} {removed} scratch test(s).")
    return 0


def keep(root: Path, name: str) -> int:
    """Promote a scratch test by dropping its `scratch_` prefix so `prune` won't remove it."""
    tests_dir = root / ".jiti" / "tests"
    for path in sorted(walk_py_files(tests_dir)) if tests_dir.exists() else []:
        imports, sections = parse_file(path.read_text())
        for key, section in sections.items():
            for scratch in _scratch_names(section.body):
                promoted = scratch.replace("test_scratch_", "test_", 1)
                if name not in (scratch, promoted):
                    continue
                body = re.sub(rf"\bdef {re.escape(scratch)}\b", f"def {promoted}", section.body)
                sections[key] = Section(key, section.spec_hash, content_hash(body), body)
                save_sections(path, imports, sections)
                print(f"kept {promoted} in {path}.")
                print(
                    "Note: regenerating this section drops it again. For a durable test, move it "
                    "into your test suite or make it a @jiti.required_for gate."
                )
                return 0
    print(f"no scratch test matching '{name}' found.")
    return 1


def clear(root: Path) -> int:
    """Delete the entire `.jiti/` mirror."""
    JitiStore(root / ".jiti").clear()
    print("cleared .jiti/.")
    return 0


def _drop_scratch(body: str) -> str:
    lines = body.splitlines()
    drop: set[int] = set()
    for node in ast.parse(body).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_scratch_"):
            continue
        start = min((dec.lineno for dec in node.decorator_list), default=node.lineno)
        drop.update(range(start, (node.end_lineno or node.lineno) + 1))
    return "\n".join(line for number, line in enumerate(lines, 1) if number not in drop).strip("\n")


def _scratch_names(body: str) -> list[str]:
    return re.findall(r"^def (test_scratch_\w+)", body, re.MULTILINE)
