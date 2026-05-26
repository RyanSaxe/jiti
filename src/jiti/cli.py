"""The `jiti` command-line tool.

Commands operate on the `.jiti/` mirror: `status` inspects it (read-only, never imports your
code), `merge` folds generated code back into your source, `test prune`/`keep` manage the
generated jiti-tests, and `clear` deletes the mirror.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

from jiti.discovery import walk_py_files
from jiti.errors import JitiError
from jiti.merge import run_merge, source_files
from jiti.store import (
    JitiStore,
    Section,
    SectionRef,
    content_hash,
    inventory,
    parse_file,
    parse_sections,
    remove_empty_dirs,
    save_sections,
    test_path_for_module,
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
    test_sections = _load_test_sections(mirror, by_module)

    edited = methods = kept_total = scratch_total = 0
    for module in sorted(by_module):
        print(_display_path(sources.get(module), module, root))
        for ref in sorted(by_module[module], key=lambda ref: ref.qualname):
            kept, scratch = _test_counts(test_sections.get(ref.key))
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


def _load_test_sections(mirror: Path, by_module: dict[str, list[SectionRef]]) -> dict[str, Section]:
    """Parse each module's companion test file once and return all sections by key."""
    out: dict[str, Section] = {}
    for module in by_module:
        try:
            text = test_path_for_module(mirror, module).read_text()
        except FileNotFoundError:
            continue
        out.update(parse_sections(text))
    return out


def _test_counts(section: Section | None) -> tuple[int, int]:
    """`(kept, scratch)` test counts in a parsed section body."""
    if section is None:
        return 0, 0
    kept = scratch = 0
    for node in ast.parse(section.body).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("test_scratch_"):
            scratch += 1
        elif node.name.startswith("test_"):
            kept += 1
    return kept, scratch


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
        file_removed = 0
        for key, section in sections.items():
            scratch = _scratch_names(section.body)
            if not scratch:
                kept[key] = section
                continue
            file_removed += len(scratch)
            body = _drop_scratch(section.body)
            if body.strip():
                kept[key] = Section(key, section.spec_hash, content_hash(body), body)
        if file_removed:
            removed += file_removed
            if not dry_run:
                save_sections(path, imports, kept)

    if not dry_run and removed:
        remove_empty_dirs(root / ".jiti")
    print(f"{'would prune' if dry_run else 'pruned'} {removed} scratch test(s).")
    return 0


def keep(root: Path, name: str) -> int:
    """Promote a scratch test by dropping its `scratch_` prefix so `prune` won't remove it."""
    tests_dir = root / ".jiti" / "tests"
    match = _find_scratch(tests_dir, name) if tests_dir.exists() else None
    if match is None:
        print(f"no scratch test matching '{name}' found.")
        return 1
    path, key, scratch, promoted = match
    imports, sections = parse_file(path.read_text())
    section = sections[key]
    body = re.sub(rf"\bdef {re.escape(scratch)}\b", f"def {promoted}", section.body)
    sections[key] = Section(key, section.spec_hash, content_hash(body), body)
    save_sections(path, imports, sections)
    print(f"kept {promoted} in {path}.")
    print(
        "Note: regenerating this section drops it again. For a durable test, move it "
        "into your test suite or make it a @jiti.required_for gate."
    )
    return 0


def _find_scratch(tests_dir: Path, name: str) -> tuple[Path, str, str, str] | None:
    """First scratch test matching `name` (its scratch_ or promoted form), with its file."""
    for path in sorted(walk_py_files(tests_dir)):
        _, sections = parse_file(path.read_text())
        for key, section in sections.items():
            for scratch in _scratch_names(section.body):
                promoted = scratch.replace("test_scratch_", "test_", 1)
                if name in (scratch, promoted):
                    return path, key, scratch, promoted
    return None


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jiti", description="Inspect and graduate jiti code.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: cwd)")
    sub = parser.add_subparsers(required=True)

    status_p = sub.add_parser("status", help="show generated sections and their state (read-only)")
    status_p.set_defaults(handler=lambda args: status(args.root))

    merge_p = sub.add_parser("merge", help="inline generated code into your source, dropping @jiti")
    merge_p.add_argument("targets", nargs="*", help="file path, dotted module, or qualname")
    merge_p.add_argument("--all", action="store_true", dest="merge_all", help="merge every section")
    merge_p.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    merge_p.set_defaults(handler=_handle_merge)

    test_p = sub.add_parser("test", help="manage generated jiti-tests")
    test_sub = test_p.add_subparsers(required=True)
    prune_p = test_sub.add_parser("prune", help="delete agent scratch tests (test_scratch_*)")
    prune_p.add_argument("--dry-run", action="store_true", help="print the count; write nothing")
    prune_p.set_defaults(handler=lambda args: prune(args.root, args.dry_run))
    keep_p = test_sub.add_parser("keep", help="promote a scratch test so prune won't drop it")
    keep_p.add_argument("name", help="the scratch test's function name")
    keep_p.set_defaults(handler=lambda args: keep(args.root, args.name))

    clear_p = sub.add_parser("clear", help="delete the .jiti/ mirror")
    clear_p.set_defaults(handler=lambda args: clear(args.root))

    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    try:
        return args.handler(args)
    except JitiError as error:
        print(f"jiti: {error}", file=sys.stderr)
        return 1


def _handle_merge(args: argparse.Namespace) -> int:
    if not args.targets and not args.merge_all:
        print("jiti merge: pass one or more targets, or --all", file=sys.stderr)
        return 2
    return run_merge(args.root, args.targets, args.merge_all, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
