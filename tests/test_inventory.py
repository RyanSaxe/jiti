"""Enumerating the mirror and resolving merge targets to sections."""

from pathlib import Path

import pytest

from jiti.errors import MergeError
from jiti.merge import select
from jiti.store import Section, inventory, parse_sections, render_file


def _write_section(root: Path, module: str, qualname: str, *, tests: bool = False) -> None:
    relpath = Path(*module.split(".")).with_suffix(".py")
    path = root / ("tests" / relpath.with_name(f"test_{relpath.name}") if tests else relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{module}.{qualname}"
    body = "def test_x():\n    ..." if tests else "def f():\n    return 1"
    sections = parse_sections(path.read_text()) if path.exists() else {}
    sections[key] = Section(key, "spec", "gen", body)
    path.write_text(render_file("", sections))


def test_inventory_lists_sections_with_module_and_qualname(tmp_path):
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")
    _write_section(root, "app.semver", "Version.bump")

    refs = {ref.key: ref for ref in inventory(root)}

    assert set(refs) == {"app.text.slugify", "app.semver.Version.bump"}
    assert refs["app.text.slugify"].module == "app.text"
    assert refs["app.text.slugify"].name == "slugify"
    assert refs["app.text.slugify"].is_method is False
    assert refs["app.semver.Version.bump"].name == "bump"
    assert refs["app.semver.Version.bump"].is_method is True


def test_inventory_excludes_the_tests_subtree(tmp_path):
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")
    _write_section(root, "app.text", "slugify", tests=True)

    assert [ref.key for ref in inventory(root)] == ["app.text.slugify"]
    assert all("tests" not in ref.impl_path.parts for ref in inventory(root))


def test_select_by_exact_qualname(tmp_path):
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")
    _write_section(root, "app.text", "titlecase")

    chosen = select(["app.text.slugify"], inventory(root))

    assert [ref.key for ref in chosen] == ["app.text.slugify"]


def test_select_by_module_returns_all_its_sections(tmp_path):
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")
    _write_section(root, "app.text", "titlecase")
    _write_section(root, "app.other", "thing")

    chosen = select(["app.text"], inventory(root))

    assert {ref.key for ref in chosen} == {"app.text.slugify", "app.text.titlecase"}


def test_select_by_file_path(tmp_path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "text.py").write_text("def slugify(): ...\n")
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")

    chosen = select([str(pkg / "text.py")], inventory(root))

    assert {ref.key for ref in chosen} == {"app.text.slugify"}


def test_select_unmatched_target_raises(tmp_path):
    root = tmp_path / ".jiti"
    _write_section(root, "app.text", "slugify")

    with pytest.raises(MergeError, match="no generated section matched"):
        select(["nope"], inventory(root))
