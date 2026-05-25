"""The `jiti` command-line tool.

Commands operate on the `.jiti/` mirror: `status` inspects it (read-only, never imports your
code), `merge` folds generated code back into your source, `test prune`/`keep` manage the
generated jiti-tests, and `clear` deletes the mirror.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from jiti.merge import source_files
from jiti.store import SectionRef, inventory, module_relpath, parse_sections


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
