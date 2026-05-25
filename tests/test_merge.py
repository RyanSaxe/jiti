"""`jiti merge` end-to-end against real importable projects in a tmp dir."""

from textwrap import dedent

from jiti.merge import run_merge
from jiti.store import inventory

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
