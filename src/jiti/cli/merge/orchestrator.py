"""Drive `jiti merge`: resolve targets, gate, dispatch source rewrites and test folding.

Gating runs in full before any write because introspecting one stub reads source by line
number — rewriting an earlier section would corrupt the next one's spec check. So we resolve
every chosen section against the intact source first, then apply the (pure-text) rewrites.
After impls land in source, test files are folded in (see `tests.py`), and ruff formats every
touched file in a single batch.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from jiti.cli.merge.source import apply_section, resolve_wrapper
from jiti.cli.merge.tests import GateLocation, gate_locations_for, merge_test_files
from jiti.core.discovery import import_file, import_test_modules, module_name, walk_py_files
from jiti.core.errors import MergeError
from jiti.core.store import (
    Action,
    JitiStore,
    SectionRef,
    inventory,
    remove_empty_dirs,
)
from jiti.core.validate import RUFF


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

    `--prune` drops the agent's scratch tests instead of ejecting them to a real test file.
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

    # `apply_section` strips @jiti from source, leaving the wrapper unreachable via import.
    # Snapshot each ref's gate locations now while the wrapper is still bound to the user's stub.
    gate_index: dict[str, list[GateLocation]] = {
        ref.key: gate_locations_for(ref) for ref, _, _ in plans
    }

    written: list[Path] = []
    for ref, source_path, action in plans:
        if not dry_run:
            written.append(apply_section(ref, source_path))
        print(f"{'would merge' if dry_run else 'merged'} {ref.key}  ({action.value})")

    if not dry_run and plans:
        written.extend(merge_test_files(root, mirror, plans, gate_index, prune_scratch))
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


def _resolve_state(ref: SectionRef, source_path: Path, store: JitiStore) -> Action:
    import_file(source_path)
    target = resolve_wrapper(ref.module, ref.qualname)
    if target is None:
        raise MergeError(f"no @jiti `{ref.qualname}` in {source_path.name}; regenerate or clear")
    return store.resolve(target.declaration()).action


def _any_jiti_left(sources: dict[str, Path]) -> bool:
    return any("@jiti" in path.read_text() for path in set(sources.values()))


def _ruff_batch(paths: Sequence[Path]) -> None:
    """Run `ruff check --fix --select F401,I` then `ruff format` over `paths` in one pass each."""
    if not paths:
        return
    args = [str(p) for p in paths]
    subprocess.run(
        [*RUFF, "check", "--fix", "--select", "F401,I", "--quiet", *args], capture_output=True
    )
    subprocess.run([*RUFF, "format", "--quiet", *args], capture_output=True)
