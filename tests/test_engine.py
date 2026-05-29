"""The agent engine: generation, caching, retry, the cycle guard, and cascade — fake client."""

import tempfile
from pathlib import Path

import pytest
from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine, _LazyAnthropic
from jiti.core.declaration import introspect
from jiti.core.errors import GenerationCycleError
from jiti.core.store import JitiStore

GOOD_IMPL = "def slugify(text: str) -> str:\n    return text.lower().replace(' ', '-')"
BAD_IMPL = "def slugify(text: str) -> str:\n    return text.upper()"
TESTS = "def test_slug():\n    assert slugify('Hello World') == 'hello-world'"


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


def test_generates_commits_and_caches(tmp_path):
    client = ScriptedClient([submit("slugify", GOOD_IMPL, TESTS)])
    engine = Engine(client=client, store=JitiStore(tmp_path / ".jiti"))

    assert jiti(engine=engine)(slugify)("Hello World") == "hello-world"
    assert client.calls == 1  # stops on the passing submit — no wrap-up turn

    fresh = jiti(engine=engine)(slugify)
    assert fresh("Hi There") == "hi-there"
    assert client.calls == 1  # cache hit — no new generation


def test_retries_until_validation_passes(tmp_path):
    client = ScriptedClient(
        [submit("slugify", BAD_IMPL, TESTS), submit("slugify", GOOD_IMPL, TESTS)]
    )
    engine = Engine(client=client, store=JitiStore(tmp_path / ".jiti"))

    assert jiti(engine=engine)(slugify)("Hello World") == "hello-world"
    assert client.calls == 2


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
        "def casc_slugify(text: str) -> str:\n"
        "    return casc_normalize(text).replace(' ', '-')"
    )
    slug_tests = "def test_s():\n    assert casc_slugify('Hi There') == 'hi-there'"
    norm_impl = "def casc_normalize(text: str) -> str:\n    return text.lower()"
    norm_tests = "def test_n():\n    assert casc_normalize('Hi') == 'hi'"
    _CASCADE_CLIENT.script = [
        submit("casc_slugify", slug_impl, slug_tests),
        submit("casc_normalize", norm_impl, norm_tests),  # nested, during slugify's test run
    ]

    assert casc_slugify("Hello World") == "hello-world"
    assert _CASCADE_CLIENT.calls == 2


_METHOD_CLIENT = ScriptedClient([])
_METHOD_ENGINE = Engine(client=_METHOD_CLIENT, store=JitiStore(Path(tempfile.mkdtemp()) / ".jiti"))


class Counter:
    def __init__(self, base: int) -> None:
        self.base = base

    @jiti(engine=_METHOD_ENGINE)
    def add(self, n: int) -> int:
        """Add n to the base."""
        ...


def test_method_generation():
    impl = "def add(self, n: int) -> int:\n    return self.base + n"
    tests = (
        f"from {__name__} import Counter\n\ndef test_add():\n    assert Counter(10).add(5) == 15"
    )
    _METHOD_CLIENT.script = [submit("add", impl, tests)]

    assert Counter(10).add(5) == 15
    assert _METHOD_CLIENT.calls == 1


def test_lazy_anthropic_defers_construction_until_first_use(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    client = _LazyAnthropic()  # building the lazy client needs no key — only generation does

    assert client._client is None  # the real Anthropic client isn't constructed yet
