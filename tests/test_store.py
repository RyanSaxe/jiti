"""The store's three-way state (untouched / edited / conflict) is the safety core."""

import inspect
import threading

import pytest
from conftest import make_declaration

from jiti.core.store import Action, JitiStore

IMPL = "def slugify():\n    return 'a'"
TESTS = "def test_slugify():\n    assert slugify() == 'a'"


@pytest.fixture
def store(tmp_path):
    return JitiStore(tmp_path / ".jiti")


def hand_edit(store, declaration, replacement):
    path = store.impl_path(declaration)
    path.write_text(path.read_text().replace("return 'a'", replacement))


def test_paths_mirror_the_source_tree(store):
    declaration = make_declaration()

    assert store.impl_path(declaration) == store.root / "app" / "text.py"
    assert store.test_path(declaration) == store.root / "tests" / "app" / "test_text.py"


def test_write_then_read_round_trips_an_untouched_section(store):
    declaration = make_declaration()
    store.write(declaration, IMPL, TESTS)

    section = store.read_section(declaration)
    assert section is not None
    assert section.body == IMPL
    assert section.spec_hash == declaration.spec_hash
    assert not section.edited


def test_companion_file_is_valid_python(store):
    declaration = make_declaration()
    store.write(declaration, IMPL, TESTS)

    compile(store.impl_path(declaration).read_text(), "<companion>", "exec")


def test_concurrent_writes_to_one_companion_keep_every_section(store):
    """Two declarations in one module committing from different threads must not race the
    read-modify-write and drop each other's sections. (Without the store's write lock this
    loses sections almost every run — the ruff post-process keeps the window wide open.)"""
    declarations = [make_declaration(qualname=f"fn{i}", def_line=f"def fn{i}():") for i in range(8)]
    barrier = threading.Barrier(len(declarations))
    errors: list[BaseException] = []

    def write(declaration) -> None:
        try:
            barrier.wait()
            body = f"def {declaration.name}():\n    return '{declaration.name}'"
            store.write(declaration, body, f"def test_{declaration.name}():\n    pass")
        except BaseException as error:  # surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=write, args=(d,)) for d in declarations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for declaration in declarations:
        assert store.read_section(declaration) is not None
        assert store.read_test_section(declaration) is not None


def test_resolve_missing_section_says_generate(store):
    assert store.resolve(make_declaration()).action is Action.GENERATE


def test_resolve_untouched_matching_spec_says_run(store):
    declaration = make_declaration()
    store.write(declaration, IMPL, TESTS)

    assert store.resolve(declaration).action is Action.RUN


def test_resolve_untouched_changed_spec_says_regenerate(store):
    store.write(make_declaration(docstring="old"), IMPL, TESTS)

    assert store.resolve(make_declaration(docstring="new")).action is Action.REGENERATE


def test_resolve_edited_matching_spec_says_run_owned(store):
    declaration = make_declaration()
    store.write(declaration, IMPL, TESTS)
    hand_edit(store, declaration, "return 'edited'")

    resolution = store.resolve(declaration)
    assert resolution.action is Action.RUN_OWNED
    assert resolution.section.edited


def test_resolve_edited_changed_spec_says_conflict(store):
    declaration = make_declaration(docstring="old")
    store.write(declaration, IMPL, TESTS)
    hand_edit(store, declaration, "return 'edited'")

    assert store.resolve(make_declaration(docstring="new")).action is Action.CONFLICT


def test_imports_are_hoisted_deduped_and_bodies_left_import_free(store):
    a = make_declaration(qualname="a")
    b = make_declaration(qualname="b")
    store.write(a, "import re\n\ndef a():\n    return re.compile('x')", "def test_a():\n    a()")
    store.write(b, "import re\n\ndef b():\n    return re.compile('y')", "def test_b():\n    b()")

    text = store.impl_path(a).read_text()
    assert text.count("import re") == 1  # deduped across sections
    assert text.index("import re") < text.index("# === jiti:")  # hoisted above the sections
    assert store.read_section(a).body == "def a():\n    return re.compile('x')"  # body import-free


def test_future_import_is_dropped_from_the_companion(store):
    """`from __future__ import annotations` in the agent's helpers stringifies every
    annotation, which breaks pydantic's runtime contract (it can't resolve names like
    `Version` from the caller's frame). Drop the directive entirely on write — the user's
    own source can use it freely; the `.jiti` file just needs live annotations."""
    declaration = make_declaration()
    store.write(
        declaration,
        "from __future__ import annotations\n\ndef slugify():\n    return 'a'",
        "def test_s():\n    slugify()",
    )

    text = store.impl_path(declaration).read_text()
    compile(text, "<companion>", "exec")
    assert "from __future__" not in text


def test_write_publishes_atomically_leaving_no_temp_files(store):
    declaration = make_declaration()
    store.write(declaration, IMPL, TESTS)

    companion = store.impl_path(declaration)
    assert list(companion.parent.iterdir()) == [companion]  # rename published; temp cleaned up


def test_multiple_declarations_share_one_companion_file(store):
    slugify = make_declaration(qualname="slugify")
    parse = make_declaration(qualname="parse")
    store.write(slugify, IMPL, TESTS)
    store.write(parse, "def parse():\n    return 1", "def test_parse():\n    assert parse() == 1")

    assert store.impl_path(slugify) == store.impl_path(parse)
    assert store.read_section(slugify).body == IMPL
    assert store.read_section(parse).body == "def parse():\n    return 1"


def test_loaded_function_has_live_annotation_objects_not_strings(store):
    """compile() inherits the caller's __future__ flags by default; store.py has
    `from __future__ import annotations`, which would silently stringify every loaded
    function's annotations and break downstream introspection (pydantic, runtime checks).
    Pin the `dont_inherit=True` fix that keeps annotations as live class objects."""
    declaration = make_declaration(
        qualname="identity",
        signature=inspect.Signature(
            parameters=[
                inspect.Parameter("x", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int)
            ],
            return_annotation=int,
        ),
    )
    store.write(
        declaration,
        "def identity(x: int) -> int:\n    return x",
        "def test_i():\n    assert identity(1) == 1",
    )

    loaded = store.load(declaration)

    assert loaded.__annotations__ == {"x": int, "return": int}
