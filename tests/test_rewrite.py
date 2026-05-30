"""The pure source rewriter: text in, text out, no filesystem or ruff."""

import ast
from textwrap import dedent

import pytest

from jiti.cli.merge.source import _is_jiti_decorator, merge_into_source
from jiti.core.errors import MergeError


def test_inlines_body_and_drops_the_decorator():
    source = dedent('''\
        from jiti import jiti


        @jiti
        def slugify(text: str) -> str:
            """Slugify."""
            ...
    ''')
    body = dedent("""\
        def slugify(text: str) -> str:
            return text.strip().lower().replace(" ", "-")
    """)

    merged = merge_into_source(source, "slugify", "app.text", body, "")

    assert "@jiti" not in merged
    assert "..." not in merged
    assert 'return text.strip().lower().replace(" ", "-")' in merged


def test_strips_self_import_but_keeps_third_party():
    source = dedent("""\
        from jiti import jiti


        @jiti
        def compare(a, b):
            ...
    """)
    body = dedent("""\
        def compare(a, b):
            return cmp_to_key(parse(a), parse(b))
    """)
    imports = "import re\nfrom app.text import parse"

    merged = merge_into_source(source, "compare", "app.text", body, imports)

    assert "import re" in merged
    assert "from app.text import parse" not in merged


def test_strips_plain_self_module_import():
    source = "from jiti import jiti\n\n\n@jiti\ndef f():\n    ...\n"
    merged = merge_into_source(
        source, "f", "app.text", "def f():\n    return 1\n", "import app.text"
    )
    assert "import app.text" not in merged


def test_async_function_merges():
    source = dedent("""\
        from jiti import jiti


        @jiti
        async def fetch(url: str) -> str:
            ...
    """)
    body = "async def fetch(url: str) -> str:\n    return await _get(url)\n"

    merged = merge_into_source(source, "fetch", "app.net", body, "")

    assert "async def fetch(url: str) -> str:" in merged
    assert "@jiti" not in merged


def test_jiti_call_decorator_with_engine_is_replaced():
    source = dedent("""\
        from jiti import jiti

        engine = ...


        @jiti(engine=engine)
        def f(x: int) -> int:
            ...
    """)
    merged = merge_into_source(source, "f", "app.m", "def f(x: int) -> int:\n    return x\n", "")
    assert "@jiti" not in merged
    assert "return x" in merged


def test_aliased_self_import_survives_merge():
    """`from own_module import x as _x` introduces a NEW name; the merged body uses the
    alias, so the import must NOT be stripped as a self-import."""
    source = dedent("""\
        from jiti import jiti


        def helper():
            return 1


        @jiti
        def f() -> int:
            ...
    """)
    body = "def f() -> int:\n    return _helper()\n"
    imports = "from app.m import helper as _helper"

    merged = merge_into_source(source, "f", "app.m", body, imports)

    assert "from app.m import helper as _helper" in merged
    assert "return _helper()" in merged


def test_unaliased_self_import_is_stripped():
    """`from own_module import x` (no alias) is redundant once merged into own_module."""
    source = dedent("""\
        from jiti import jiti


        def helper():
            return 1


        @jiti
        def f() -> int:
            ...
    """)
    body = "def f() -> int:\n    return helper()\n"

    merged = merge_into_source(source, "f", "app.m", body, "from app.m import helper")

    assert "from app.m import helper" not in merged


def test_section_helpers_land_at_module_level_not_inside_class():
    """A class-method section body that brings helpers (constants, imports) must put them
    at module level on merge — not inline at the splice site, where they'd land inside
    the class body and break syntax under decorators like `@staticmethod`."""
    source = dedent("""\
        from jiti import jiti


        class Box:
            @jiti
            def is_valid(self) -> bool:
                ...
    """)
    body = "_PATTERN = 42\n\ndef is_valid(self) -> bool:\n    return _PATTERN > 0\n"

    merged = merge_into_source(source, "Box.is_valid", "app.m", body, "")

    # Helper landed at module level (before the class), not nested inside it.
    assert merged.index("_PATTERN = 42") < merged.index("class Box")
    # The def itself stays inside the class (indented one level).
    assert "    def is_valid" in merged


def test_non_jiti_decorator_above_jiti_is_preserved():
    """Decorators stacked above `@jiti` (e.g. `@functools.cache`, `@staticmethod`) survive
    the merge — only the `@jiti` line itself is dropped."""
    source = dedent("""\
        import functools

        from jiti import jiti


        @functools.cache
        @jiti
        def f(x: int) -> int:
            ...
    """)
    merged = merge_into_source(source, "f", "app.m", "def f(x: int) -> int:\n    return x\n", "")

    assert "@jiti" not in merged
    assert "@functools.cache" in merged
    assert merged.index("@functools.cache") < merged.index("def f")
    assert "return x" in merged


def test_no_jiti_decorator_at_all_is_rejected():
    source = dedent("""\
        import functools


        @functools.cache
        def f(x: int) -> int:
            ...
    """)
    with pytest.raises(MergeError, match="no @jiti-decorated"):
        merge_into_source(source, "f", "app.m", "def f(x: int) -> int:\n    return x\n", "")


def test_multiline_signature_is_spliced_whole():
    source = dedent('''\
        from jiti import jiti


        @jiti
        def build(
            name: str,
            count: int,
        ) -> str:
            """Build."""
            ...
    ''')
    body = "def build(name: str, count: int) -> str:\n    return name * count\n"

    merged = merge_into_source(source, "build", "app.m", body, "")

    assert "return name * count" in merged
    assert merged.count("def build") == 1


def test_inserts_imports_after_module_docstring_when_no_imports():
    source = dedent('''\
        """Module docstring."""

        from jiti import jiti


        @jiti
        def f() -> int:
            ...
    ''')
    merged = merge_into_source(
        source, "f", "app.m", "def f() -> int:\n    return re.match\n", "import re"
    )
    lines = merged.splitlines()
    assert lines[0] == '"""Module docstring."""'
    assert "import re" in merged


def test_preserves_surrounding_code_verbatim():
    source = dedent("""\
        from jiti import jiti

        CONSTANT = 42  # keep me


        def helper() -> int:
            return CONSTANT


        @jiti
        def f() -> int:
            ...


        # trailing comment
        def after() -> int:
            return 1
    """)
    body = "def f() -> int:\n    return helper()\n"

    merged = merge_into_source(source, "f", "app.m", body, "")

    assert "CONSTANT = 42  # keep me" in merged
    assert "# trailing comment" in merged
    assert "def after() -> int:" in merged
    assert "return helper()" in merged


def test_missing_target_raises():
    source = "from jiti import jiti\n\n\n@jiti\ndef f():\n    ...\n"
    with pytest.raises(MergeError, match="no @jiti-decorated `ghost`"):
        merge_into_source(source, "ghost", "app.m", "def ghost(): ...\n", "")


def test_overload_siblings_are_ignored():
    source = dedent("""\
        from typing import overload

        from jiti import jiti


        @overload
        def f(x: int) -> int: ...
        @overload
        def f(x: str) -> str: ...
        @jiti
        def f(x):
            ...
    """)
    body = "def f(x):\n    return x\n"

    merged = merge_into_source(source, "f", "app.m", body, "")

    assert "@overload" in merged  # the typing overloads are untouched
    assert "return x" in merged
    assert "@jiti" not in merged


def test_ambiguous_duplicate_jiti_defs_raise():
    source = dedent("""\
        from jiti import jiti


        @jiti
        def f():
            ...


        @jiti
        def f():
            ...
    """)
    with pytest.raises(MergeError, match="2 @jiti `f`"):
        merge_into_source(source, "f", "app.m", "def f(): ...\n", "")


def _first_decorator(source: str) -> ast.expr:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef)
    return node.decorator_list[0]


def test_is_jiti_decorator_peels_call_and_attribute():
    assert _is_jiti_decorator(_first_decorator("@jiti\ndef f(): ..."))
    assert _is_jiti_decorator(_first_decorator("@jiti(engine=e)\ndef f(): ..."))
    assert not _is_jiti_decorator(_first_decorator("@other\ndef f(): ..."))
