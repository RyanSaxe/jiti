"""End-to-end CLI: argparse wiring, dispatch, and exit codes via `main(argv)`."""

from textwrap import dedent

import pytest

from jiti.cli import main
from jiti.store import Section, content_hash, module_relpath, render_file

STUB = dedent('''\
    from jiti import jiti


    @jiti
    def f() -> int:
        """F."""
        ...
''')


def test_requires_a_subcommand():
    with pytest.raises(SystemExit):
        main([])


def test_status_on_empty_project(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "status"]) == 0
    assert "no generated code" in capsys.readouterr().out


def test_merge_without_target_or_all_errors(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "merge"]) == 2
    assert "pass one or more targets" in capsys.readouterr().err


def test_merge_all_via_main(proj, capsys):
    module = proj.module("m", STUB)
    proj.generate(module, "f", "def f() -> int:\n    return 1")

    assert main(["--root", str(proj.root), "merge", "--all"]) == 0

    source = proj.source_of(module).read_text()
    assert "@jiti" not in source
    assert "return 1" in source


def test_test_prune_via_main(tmp_path, capsys):
    relpath = module_relpath("app.text")
    path = tmp_path / ".jiti" / "tests" / relpath.with_name(f"test_{relpath.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "def test_scratch_x():\n    assert True"
    key = "app.text.slugify"
    path.write_text(render_file("", {key: Section(key, "s", content_hash(body), body)}))

    assert main(["--root", str(tmp_path), "test", "prune"]) == 0
    assert "pruned 1" in capsys.readouterr().out


def test_clear_via_main(tmp_path):
    (tmp_path / ".jiti").mkdir()
    (tmp_path / ".jiti" / "marker.py").write_text("x = 1\n")

    assert main(["--root", str(tmp_path), "clear"]) == 0
    assert not (tmp_path / ".jiti").exists()
