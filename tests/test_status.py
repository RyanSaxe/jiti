"""`jiti status` — disk-only inspection of the mirror."""

from pathlib import Path

from jiti import cli
from jiti.store import Section, content_hash, module_relpath, parse_sections, render_file


def _write_impl(mirror: Path, module: str, qualname: str, *, edited: bool = False) -> None:
    path = mirror / module_relpath(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{module}.{qualname}"
    body = "def f():\n    return 1"
    gen_hash = "stale" if edited else content_hash(body)
    sections = parse_sections(path.read_text()) if path.exists() else {}
    sections[key] = Section(key, "spec", gen_hash, body)
    path.write_text(render_file("", sections))


def _write_tests(mirror: Path, module: str, qualname: str, *, kept: int, scratch: int) -> None:
    relpath = module_relpath(module)
    path = mirror / "tests" / relpath.with_name(f"test_{relpath.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{module}.{qualname}"
    fns = [f"def test_keep_{i}():\n    assert True" for i in range(kept)]
    fns += [f"def test_scratch_{i}():\n    assert True" for i in range(scratch)]
    body = "\n\n\n".join(fns)
    path.write_text(render_file("", {key: Section(key, "spec", content_hash(body), body)}))


def test_reports_state_and_test_counts(tmp_path, capsys):
    mirror = tmp_path / ".jiti"
    _write_impl(mirror, "app.text", "slugify")
    _write_impl(mirror, "app.text", "titlecase", edited=True)
    _write_tests(mirror, "app.text", "slugify", kept=1, scratch=3)

    assert cli.status(tmp_path) == 0
    out = capsys.readouterr().out

    assert "slugify" in out and "clean" in out
    assert "titlecase" in out and "edited" in out
    assert "1 kept, 3 scratch" in out


def test_displays_methods_alongside_functions(tmp_path, capsys):
    _write_impl(tmp_path / ".jiti", "app.m", "Version.bump")

    cli.status(tmp_path)
    out = capsys.readouterr().out

    assert "Version.bump" in out
    assert "clean" in out
    assert "not supported" not in out
    assert "1 method(s)" in out  # still surfaced in the summary line


def test_does_not_import_source_modules(tmp_path, capsys):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "boom.py").write_text("raise RuntimeError('should not import')\n")
    _write_impl(tmp_path / ".jiti", "app.boom", "f")

    assert cli.status(tmp_path) == 0  # would raise if status imported app.boom
    assert "app/boom.py" in capsys.readouterr().out


def test_empty_mirror(tmp_path, capsys):
    assert cli.status(tmp_path) == 0
    assert "no generated code" in capsys.readouterr().out
