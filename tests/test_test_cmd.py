"""`jiti test prune`/`keep` and `jiti clear`."""

from pathlib import Path

from jiti import cli
from jiti.core.store import Section, content_hash, module_relpath, parse_sections, render_file

TESTS = """\
def test_keep_me():
    assert True


def test_scratch_one():
    assert 1 == 1


def test_scratch_two():
    assert 2 == 2
"""


def _write_tests(mirror: Path, module: str, qualname: str, body: str) -> Path:
    relpath = module_relpath(module)
    path = mirror / "tests" / relpath.with_name(f"test_{relpath.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"{module}.{qualname}"
    path.write_text(render_file("", {key: Section(key, "spec", content_hash(body), body)}))
    return path


def _section_body(path: Path, key: str) -> str:
    return parse_sections(path.read_text())[key].body


def test_prune_drops_scratch_keeps_authored(tmp_path, capsys):
    mirror = tmp_path / ".jiti"
    path = _write_tests(mirror, "app.text", "slugify", TESTS)

    assert cli.prune(tmp_path, dry_run=False) == 0
    assert "pruned 2 scratch test(s)" in capsys.readouterr().out

    body = _section_body(path, "app.text.slugify")
    assert "def test_keep_me" in body
    assert "test_scratch_" not in body


def test_prune_deletes_a_section_with_only_scratch(tmp_path):
    mirror = tmp_path / ".jiti"
    only_scratch = "def test_scratch_a():\n    assert True"
    path = _write_tests(mirror, "app.text", "slugify", only_scratch)

    cli.prune(tmp_path, dry_run=False)

    assert not path.exists()  # emptied file removed, dirs cascade away


def test_prune_dry_run_changes_nothing(tmp_path, capsys):
    mirror = tmp_path / ".jiti"
    path = _write_tests(mirror, "app.text", "slugify", TESTS)
    before = path.read_text()

    assert cli.prune(tmp_path, dry_run=True) == 0
    assert "would prune 2" in capsys.readouterr().out
    assert path.read_text() == before


def test_keep_unscratches_a_named_test_and_warns(tmp_path, capsys):
    mirror = tmp_path / ".jiti"
    path = _write_tests(mirror, "app.text", "slugify", TESTS)

    assert cli.keep(tmp_path, "test_scratch_one") == 0
    out = capsys.readouterr().out
    assert "kept test_one" in out
    assert "durable" in out or "@jiti.required_for" in out

    body = _section_body(path, "app.text.slugify")
    assert "def test_one(" in body
    assert "def test_scratch_one(" not in body
    assert "def test_scratch_two(" in body  # the other scratch is untouched


def test_keep_accepts_the_promoted_name(tmp_path):
    mirror = tmp_path / ".jiti"
    path = _write_tests(mirror, "app.text", "slugify", TESTS)

    assert cli.keep(tmp_path, "test_two") == 0
    assert "def test_two(" in _section_body(path, "app.text.slugify")


def test_keep_missing_test_reports_and_fails(tmp_path, capsys):
    _write_tests(tmp_path / ".jiti", "app.text", "slugify", TESTS)

    assert cli.keep(tmp_path, "test_nope") == 1
    assert "no scratch test matching" in capsys.readouterr().out


def test_clear_removes_the_mirror(tmp_path, capsys):
    _write_tests(tmp_path / ".jiti", "app.text", "slugify", TESTS)

    assert cli.clear(tmp_path) == 0
    assert not (tmp_path / ".jiti").exists()
    assert "cleared" in capsys.readouterr().out
