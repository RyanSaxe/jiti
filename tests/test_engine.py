"""The agent engine: generation, caching, retry, the cycle guard, and cascade — fake client."""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jiti import jiti
from jiti.declaration import introspect
from jiti.engine import Engine
from jiti.errors import GenerationCycleError
from jiti.store import JitiStore


@dataclass
class Block:
    type: str
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class Response:
    content: list[Block]


class ScriptedClient:
    """Returns canned responses in order; only `messages.create(...)` is used."""

    def __init__(self, script: list[Response]) -> None:
        self.script = script
        self.calls = 0

    @property
    def messages(self) -> "ScriptedClient":
        return self

    def create(self, **kwargs: Any) -> Response:
        response = self.script[self.calls]
        self.calls += 1
        return response


def submit(name: str, impl: str, tests: str) -> Response:
    use = Block(
        type="tool_use", id=f"t-{name}", name="submit", input={"impl": impl, "tests": tests}
    )
    return Response(content=[use])


def done() -> Response:
    return Response(content=[Block(type="text", text="done")])


GOOD_IMPL = "def slugify(text):\n    return text.lower().replace(' ', '-')"
BAD_IMPL = "def slugify(text):\n    return text.upper()"
TESTS = "def test_slug():\n    assert slugify('Hello World') == 'hello-world'"


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


def test_generates_commits_and_caches(tmp_path):
    client = ScriptedClient([submit("slugify", GOOD_IMPL, TESTS), done()])
    engine = Engine(client=client, store=JitiStore(tmp_path / ".jiti"))

    assert jiti(engine=engine)(slugify)("Hello World") == "hello-world"
    assert client.calls == 2

    fresh = jiti(engine=engine)(slugify)
    assert fresh("Hi There") == "hi-there"
    assert client.calls == 2  # cache hit — no new generation


def test_retries_until_validation_passes(tmp_path):
    client = ScriptedClient(
        [submit("slugify", BAD_IMPL, TESTS), submit("slugify", GOOD_IMPL, TESTS), done()]
    )
    engine = Engine(client=client, store=JitiStore(tmp_path / ".jiti"))

    assert jiti(engine=engine)(slugify)("Hello World") == "hello-world"
    assert client.calls == 3


def test_cycle_guard_raises(tmp_path):
    engine = Engine(client=ScriptedClient([]), store=JitiStore(tmp_path / ".jiti"))
    declaration = introspect(slugify)
    engine._in_progress.add(declaration.key)

    with pytest.raises(GenerationCycleError):
        engine.implement(declaration, ("x",), {})


_CASCADE_CLIENT = ScriptedClient([])
_CASCADE_ENGINE = Engine(
    client=_CASCADE_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti")
)


@jiti(engine=_CASCADE_ENGINE)
def casc_normalize(text: str) -> str:
    """Lowercase the text."""
    ...


@jiti(engine=_CASCADE_ENGINE)
def casc_slugify(text: str) -> str:
    """Normalize, then hyphenate into a slug."""
    ...


def test_cascade_generates_the_callee():
    slug_impl = (
        f"from {__name__} import casc_normalize\n\n"
        "def casc_slugify(text):\n    return casc_normalize(text).replace(' ', '-')"
    )
    slug_tests = "def test_s():\n    assert casc_slugify('Hi There') == 'hi-there'"
    norm_impl = "def casc_normalize(text):\n    return text.lower()"
    norm_tests = "def test_n():\n    assert casc_normalize('Hi') == 'hi'"
    _CASCADE_CLIENT.script = [
        submit("casc_slugify", slug_impl, slug_tests),
        submit("casc_normalize", norm_impl, norm_tests),
        done(),  # normalize finishes (nested)
        done(),  # slugify finishes
    ]

    assert casc_slugify("Hello World") == "hello-world"
    assert _CASCADE_CLIENT.calls == 4
