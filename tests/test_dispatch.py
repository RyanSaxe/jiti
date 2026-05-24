"""End to end: @jiti generates, caches, runs, and respects hand edits."""

import pytest

from jiti import jiti
from jiti.declaration import introspect
from jiti.store import JitiStore
from jiti.strategy import Codegen


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


GOOD = (
    "=== IMPL ===\n"
    "```python\n"
    "def slugify(text: str) -> str:\n"
    "    return text.lower().replace(' ', '-')\n"
    "```\n"
    "=== TESTS ===\n"
    "```python\n"
    "def test_basic():\n"
    "    assert slugify('Hello World') == 'hello-world'\n"
    "```"
)


class _Model:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, prompt: str) -> str:
        return self.response


class CountingFactory:
    """Builds a model on demand and counts how often it does so (proxy for generation)."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.builds = 0

    def __call__(self) -> _Model:
        self.builds += 1
        return _Model(self.response)


@pytest.fixture
def codegen(tmp_path):
    return Codegen(model_factory=CountingFactory(GOOD), store=JitiStore(tmp_path / ".jiti"))


def test_decorated_function_generates_and_runs(codegen):
    fn = jiti(strategy=codegen)(slugify)

    assert fn("Hello World") == "hello-world"
    assert codegen.store.read_section(introspect(slugify)) is not None
    assert codegen.model_factory.builds == 1


def test_a_fresh_wrapper_hits_the_cache_without_regenerating(codegen):
    jiti(strategy=codegen)(slugify)("seed")  # generate + commit

    fresh = jiti(strategy=codegen)(slugify)

    assert fresh("A B") == "a-b"
    assert codegen.model_factory.builds == 1


def test_hand_edited_implementation_runs_as_owned(codegen):
    jiti(strategy=codegen)(slugify)("seed")  # generate + commit
    path = codegen.store.impl_path(introspect(slugify))
    path.write_text(path.read_text().replace('.replace(" ", "-")', '.replace(" ", "_")'))

    fresh = jiti(strategy=codegen)(slugify)

    assert fresh("Hello World") == "hello_world"
    assert codegen.model_factory.builds == 1
