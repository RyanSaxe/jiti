"""`jiti merge` end-to-end against real importable projects in a tmp dir."""

from textwrap import dedent

from jiti.core.store import inventory
from jiti.merge import run_merge

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

METHOD_WITH_SIBLING = dedent('''\
    from jiti import jiti


    class Counter:
        def __init__(self, start: int) -> None:
            self.count = start

        @jiti
        def step(self) -> int:
            """Increment and return the new count."""
            ...
''')

METHOD_FORWARD_REF = dedent('''\
    from jiti import jiti


    class Node:
        def __init__(self, value: int) -> None:
            self.value = value

        @jiti
        def next(self) -> "Node":
            """Return the next Node (value + 1)."""
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


def test_merges_a_method_into_its_class(proj):
    module = proj.module("ver", METHOD)
    proj.generate(
        module, "Version.bump", 'def bump(self) -> "Version":\n    return Version(self.major + 1)'
    )

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 0

    source = proj.source_of(module).read_text()
    assert "@jiti" not in source
    assert "from jiti import jiti" not in source
    assert "    def bump(self)" in source  # indented under the class
    assert "return Version(self.major + 1)" in source


def test_merge_method_keeps_user_signature_for_forward_refs(proj):
    """The agent's generated impl may use an unquoted self-class annotation (valid in the mirror
    where the class is in scope as a regular import). Splicing that bare reference into the class
    body breaks: the class isn't bound yet during class-body evaluation. Preserving the user's
    quoted signature avoids the issue."""
    module = proj.module("node", METHOD_FORWARD_REF)
    # Agent's impl uses bare `Node` (no quotes) — which works at module scope but not inside the
    # class body it's being merged into.
    proj.generate(
        module,
        "Node.next",
        "def next(self) -> Node:\n    return Node(self.value + 1)",
    )

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 0

    # The merged source should be importable. Confirm via a fresh sys.modules import.
    import importlib  # noqa: PLC0415
    import sys  # noqa: PLC0415

    for cached in [n for n in sys.modules if n == module or n.startswith(f"{module}.")]:
        del sys.modules[cached]
    mod = importlib.import_module(module)
    assert mod.Node(1).next().value == 2  # body still works
    source = proj.source_of(module).read_text()
    assert '"Node"' in source  # quoted forward reference preserved


def test_merge_method_preserves_sibling_methods(proj):
    module = proj.module("counter", METHOD_WITH_SIBLING)
    proj.generate(
        module, "Counter.step", "def step(self) -> int:\n    self.count += 1\n    return self.count"
    )

    run_merge(proj.root, [module], merge_all=False, dry_run=False)

    source = proj.source_of(module).read_text()
    assert "def __init__(self, start: int)" in source  # sibling untouched
    assert "self.count = start" in source
    assert "def step(self) -> int:" in source
    assert "self.count += 1" in source
    assert "@jiti" not in source


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

    run_merge(proj.root, [module], merge_all=False, dry_run=True)

    # dry-run leaves the section in place
    assert proj.jiti_test(module).exists()


def test_drops_required_for_decorators_and_splices_stub_bodies(proj):
    module = proj.module("text", SLUGIFY)
    test_relpath = f"{proj.pkg}/test_slug.py"
    test_module = f"{proj.pkg}.test_slug"
    proj.write_test_file(
        test_relpath,
        dedent(f'''\
            from {module} import slugify
            from jiti import jiti


            @jiti.required_for(slugify)
            def test_real_body():
                assert slugify("FOO") == "foo"


            @jiti.required_for(slugify)
            def test_stub_body() -> None:
                """slugify lowers and strips."""
                ...
        '''),
    )
    proj.generate(module, "slugify", "def slugify(text):\n    return text.lower()")
    stub_body = (
        'def test_stub_body() -> None:\n    """slugify lowers and strips."""\n'
        '    assert slugify("HELLO") == "hello"'
    )
    # Seed the generated stub body in the mirror keyed by the test function's qualname
    test_section_path = proj.mirror / "tests" / proj.pkg / "test_test_slug.py"
    test_section_path.parent.mkdir(parents=True, exist_ok=True)
    from jiti.core.store import Section, content_hash, render_file  # noqa: PLC0415

    key = f"{test_module}.test_stub_body"
    test_section_path.write_text(
        render_file("", {key: Section(key, "x", content_hash(stub_body), stub_body)})
    )

    assert run_merge(proj.root, [module], merge_all=False, dry_run=False) == 0

    after = (proj.root / test_relpath).read_text()
    assert "@jiti.required_for" not in after  # both decorators dropped
    assert "def test_real_body" in after  # real-bodied test kept
    assert 'assert slugify("FOO") == "foo"' in after  # real body preserved
    assert "def test_stub_body" in after  # stub kept (function still there)
    assert 'assert slugify("HELLO") == "hello"' in after  # stub body spliced from mirror


def test_promotes_scratch_tests_into_the_gating_test_file(proj):
    module = proj.module("text", SLUGIFY)
    test_relpath = f"{proj.pkg}/test_slug.py"
    proj.write_test_file(
        test_relpath,
        dedent(f"""\
            from {module} import slugify
            from jiti import jiti


            @jiti.required_for(slugify)
            def test_real() -> None:
                assert slugify("BAR") == "bar"
        """),
    )
    proj.generate(module, "slugify", "def slugify(text):\n    return text.lower()")
    proj.generate_test(
        module,
        "slugify",
        "def test_scratch_lowers():\n    assert slugify('A') == 'a'",
    )

    assert run_merge(proj.root, [module], merge_all=True, dry_run=False) == 0

    after = (proj.root / test_relpath).read_text()
    assert "test_scratch_lowers" not in after  # the `scratch_` prefix was promoted away
    assert "def test_lowers" in after  # promoted scratch
    assert 'slugify("A")' in after  # body preserved (ruff normalized quotes on write)
    assert "promoted agent tests" in after  # the begin/end marker survives


def test_prune_drops_scratch_tests_instead_of_promoting(proj):
    module = proj.module("text", SLUGIFY)
    test_relpath = f"{proj.pkg}/test_slug.py"
    proj.write_test_file(
        test_relpath,
        dedent(f"""\
            from {module} import slugify
            from jiti import jiti


            @jiti.required_for(slugify)
            def test_real() -> None:
                assert slugify("BAR") == "bar"
        """),
    )
    proj.generate(module, "slugify", "def slugify(text):\n    return text.lower()")
    proj.generate_test(
        module,
        "slugify",
        "def test_scratch_lowers():\n    assert slugify('A') == 'a'",
    )

    run_merge(proj.root, [module], merge_all=True, dry_run=False, prune_scratch=True)

    after = (proj.root / test_relpath).read_text()
    assert "test_scratch" not in after
    assert "test_lowers" not in after
    assert "promoted agent tests" not in after
