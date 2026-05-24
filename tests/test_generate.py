"""The repair loop: generate, validate, and feed failures back until green."""

import pytest

from jiti.declaration import introspect
from jiti.errors import GenerationError
from jiti.generate import generate, parse_response


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...


GOOD_IMPL = 'def slugify(text: str) -> str:\n    return text.lower().replace(" ", "-")'
GOOD_TESTS = 'def test_basic():\n    assert slugify("Hello World") == "hello-world"'
BAD_IMPL = "def slugify(text: str) -> str:\n    return text.upper()"


def response(impl: str, tests: str) -> str:
    return f"=== IMPL ===\n```python\n{impl}\n```\n=== TESTS ===\n```python\n{tests}\n```"


class SequenceModel:
    """Returns canned responses in order, repeating the last one once exhausted."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_parse_response_extracts_both_blocks():
    impl, tests = parse_response(response(GOOD_IMPL, GOOD_TESTS))

    assert impl == GOOD_IMPL
    assert tests == GOOD_TESTS


def test_generate_returns_validated_code(tmp_path):
    model = SequenceModel(response(GOOD_IMPL, GOOD_TESTS))

    result = generate(introspect(slugify), model, tmp_path, max_attempts=2)

    assert "def slugify(text: str) -> str:" in result.impl_source
    assert "test_basic" in result.test_source
    assert model.calls == 1


def test_generate_repairs_a_failing_first_attempt(tmp_path):
    model = SequenceModel(response(BAD_IMPL, GOOD_TESTS), response(GOOD_IMPL, GOOD_TESTS))

    result = generate(introspect(slugify), model, tmp_path, max_attempts=3)

    assert model.calls == 2
    assert "lower" in result.impl_source


def test_generate_raises_after_exhausting_attempts(tmp_path):
    model = SequenceModel(response(BAD_IMPL, GOOD_TESTS))

    with pytest.raises(GenerationError):
        generate(introspect(slugify), model, tmp_path, max_attempts=2)
