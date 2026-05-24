"""Methods: the decorator captures the owner and binds, and generation handles `self`."""

import importlib
import textwrap
from collections.abc import Callable

from jiti import jiti
from jiti.declaration import ClassContext, Declaration, introspect
from jiti.store import JitiStore
from jiti.strategy import Codegen


def test_decorator_captures_owner_and_dispatches_methods():
    seen: list[ClassContext | None] = []

    class FakeStrategy:
        def implement(self, declaration: Declaration) -> Callable[..., int]:
            seen.append(declaration.class_context)
            return lambda instance, n: instance.base + n

    class Calc:
        def __init__(self, base: int) -> None:
            self.base = base

        @jiti(strategy=FakeStrategy())
        def add(self, n: int) -> int:
            """Add n to base."""
            ...

    assert Calc(10).add(5) == 15

    context = seen[0]
    assert context is not None
    assert context.name == "Calc"


METHOD_RESPONSE = (
    "=== IMPL ===\n```python\n"
    "def add(self, n: int) -> int:\n    return self.base + n\n"
    "```\n=== TESTS ===\n```python\n"
    "def test_adds_to_base():\n    calc = Calc(10)\n    assert calc.add(5) == 15\n"
    "```"
)


class _Model:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, prompt: str) -> str:
        return self.response


def test_method_generation_end_to_end(tmp_path, monkeypatch):
    (tmp_path / "jiti_demo_calc.py").write_text(
        textwrap.dedent("""\
        class Calc:
            def __init__(self, base: int) -> None:
                self.base = base

            def add(self, n: int) -> int:
                \"\"\"Add n to base.\"\"\"
                ...
        """)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(__import__("sys").modules, "jiti_demo_calc", raising=False)
    calc = importlib.import_module("jiti_demo_calc")
    klass = calc.Calc

    declaration = introspect(klass.add, owner=klass)
    codegen = Codegen(
        model_factory=lambda: _Model(METHOD_RESPONSE),
        store=JitiStore(tmp_path / ".jiti"),
    )

    impl = codegen.implement(declaration)

    assert impl(klass(10), 5) == 15
