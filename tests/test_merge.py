"""`jiti merge` end-to-end against real importable projects in a tmp dir."""

import sys
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from uuid import uuid4

import pytest

from jiti.discovery import import_file
from jiti.merge import run_merge
from jiti.store import (
    Section,
    content_hash,
    inventory,
    module_relpath,
    parse_file,
    render_file,
)

SLUGIFY = dedent('''\
    from jiti import jiti


    @jiti
    def slugify(text: str) -> str:
        """Slugify."""
        ...
''')

TWO = dedent('''\
    from jiti import jiti


    @jiti
    def a() -> int:
        """A."""
        ...


    @jiti
    def b() -> int:
        """B."""
        ...
''')

METHOD = dedent('''\
    from dataclasses import dataclass

    from jiti import jiti


    @dataclass
    class Version:
        major: int

        @jiti
        def bump(self) -> "Version":
            """Bump."""
            ...
''')


@dataclass
class _Project:
    root: Path
    pkg: str

    @property
    def mirror(self) -> Path:
        return self.root / ".jiti"

    def module(self, name: str, source: str) -> str:
        (self.root / self.pkg / f"{name}.py").write_text(source)
        return f"{self.pkg}.{name}"

    def source_of(self, module: str) -> Path:
        return self.root / module_relpath(module)

    def jiti_impl(self, module: str) -> Path:
        return self.mirror / module_relpath(module)

    def jiti_test(self, module: str) -> Path:
        relpath = module_relpath(module)
        return self.mirror / "tests" / relpath.with_name(f"test_{relpath.name}")

    def _spec_hash(self, module: str, name: str) -> str:
        import_file(self.source_of(module))
        return getattr(sys.modules[module], name).declaration().spec_hash

    def generate(
        self,
        module: str,
        name: str,
        body: str,
        *,
        imports: str = "",
        spec_hash: str | None = None,
        edited: bool = False,
    ) -> None:
        spec = spec_hash if spec_hash is not None else self._spec_hash(module, name)
        gen = "stale" if edited else content_hash(body)
        path = self.jiti_impl(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_imports, sections = parse_file(path.read_text()) if path.exists() else ("", {})
        combined = "\n".join(part for part in (existing_imports, imports) if part)
        key = f"{module}.{name}"
        sections[key] = Section(key, spec, gen, body)
        path.write_text(render_file(combined, sections))

    def generate_test(self, module: str, name: str, body: str) -> None:
        path = self.jiti_test(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = f"{module}.{name}"
        path.write_text(render_file("", {key: Section(key, "s", content_hash(body), body)}))


@pytest.fixture
def proj(tmp_path):
    pkg = f"proj_{uuid4().hex[:8]}"
    (tmp_path / pkg).mkdir()
    (tmp_path / pkg / "__init__.py").write_text("")
    yield _Project(tmp_path, pkg)
    for name in [n for n in sys.modules if n == pkg or n.startswith(f"{pkg}.")]:
        del sys.modules[name]
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def test_inlines_body_and_removes_the_section(proj):
    module = proj.module("text", SLUGIFY)
    proj.generate(module, "slugify", "def slugify(text: str) -> str:\n    return text.lower()")

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 0

    source = proj.source_of(module).read_text()
    assert "@jiti" not in source
    assert "return text.lower()" in source
    assert "from jiti import jiti" not in source
    assert not proj.mirror.exists()  # last section merged → mirror cascades away


def test_folds_a_hand_edited_body(proj):
    module = proj.module("text", SLUGIFY)
    proj.generate(
        module, "slugify", 'def slugify(text: str) -> str:\n    return "EDIT-" + text', edited=True
    )

    run_merge(proj.root, [module], merge_all=False, dry_run=False)

    assert "EDIT-" in proj.source_of(module).read_text()


def test_blocks_when_spec_drifted(proj, capsys):
    module = proj.module("text", SLUGIFY)
    proj.generate(module, "slugify", "def slugify(text):\n    return text", spec_hash="drift")

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 1
    assert "out of sync" in capsys.readouterr().out
    assert "@jiti" in proj.source_of(module).read_text()  # untouched


def test_blocks_edited_and_drifted_conflict(proj, capsys):
    module = proj.module("text", SLUGIFY)
    proj.generate(
        module, "slugify", "def slugify(text):\n    return text", spec_hash="drift", edited=True
    )

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 1
    assert "out of sync" in capsys.readouterr().out


def test_method_is_rejected_with_a_clear_message(proj, capsys):
    module = proj.module("ver", METHOD)
    proj.generate(module, "Version.bump", "def bump(self): ...", spec_hash="x")

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 1
    assert "methods is not supported" in capsys.readouterr().out


def test_dry_run_writes_nothing(proj, capsys):
    module = proj.module("text", SLUGIFY)
    proj.generate(module, "slugify", "def slugify(text):\n    return text")
    source_before = proj.source_of(module).read_text()
    jiti_before = proj.jiti_impl(module).read_text()

    assert run_merge(proj.root, [module], merge_all=False, dry_run=True) == 0

    assert "would merge" in capsys.readouterr().out
    assert proj.source_of(module).read_text() == source_before
    assert proj.jiti_impl(module).read_text() == jiti_before


def test_partial_merge_keeps_other_stub_and_import(proj):
    module = proj.module("two", TWO)
    proj.generate(module, "a", "def a() -> int:\n    return 1")
    proj.generate(module, "b", "def b() -> int:\n    return 2")

    run_merge(proj.root, [f"{module}.a"], merge_all=False, dry_run=False)

    source = proj.source_of(module).read_text()
    assert "return 1" in source
    assert "@jiti" in source  # b is still a stub
    assert "from jiti import jiti" in source  # still needed by b
    assert {ref.key for ref in inventory(proj.mirror)} == {f"{module}.b"}


def test_merge_all_inlines_everything_and_drops_the_import(proj, capsys):
    module = proj.module("two", TWO)
    proj.generate(module, "a", "def a() -> int:\n    return 1")
    proj.generate(module, "b", "def b() -> int:\n    return 2")

    assert run_merge(proj.root, [], merge_all=True, dry_run=False) == 0

    source = proj.source_of(module).read_text()
    assert "@jiti" not in source
    assert "from jiti import jiti" not in source
    assert not proj.mirror.exists()
    assert "drop jiti" in capsys.readouterr().out


def test_strips_self_import_in_the_merged_source(proj):
    module = proj.module("two", TWO)
    proj.generate(module, "a", "def a() -> int:\n    return b()", imports=f"from {module} import b")
    proj.generate(module, "b", "def b() -> int:\n    return 2")

    run_merge(proj.root, [f"{module}.a"], merge_all=False, dry_run=False)

    source = proj.source_of(module).read_text()
    assert f"from {module} import b" not in source
    assert "return b()" in source


def test_removes_the_test_section_too(proj):
    module = proj.module("text", SLUGIFY)
    proj.generate(module, "slugify", "def slugify(text):\n    return text")
    proj.generate_test(module, "slugify", "def test_scratch_x():\n    assert True")

    run_merge(proj.root, [module], merge_all=False, dry_run=False)

    assert not proj.jiti_test(module).exists()
