"""The validator is the repair loop's judge: ruff + ty + pytest on a candidate."""

import pytest

from jiti.validate import validate

CORRECT = 'def slugify(text: str) -> str:\n    return text.lower().replace(" ", "-")'
TESTS = 'def test_basic():\n    assert slugify("Hello World") == "hello-world"'


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def test_accepts_a_correct_implementation(workdir):
    result = validate("slugify", CORRECT, TESTS, workdir)

    assert result.ok
    assert result.report == ""


def test_reports_failing_tests(workdir):
    wrong = "def slugify(text: str) -> str:\n    return text.upper()"

    result = validate("slugify", wrong, TESTS, workdir)

    assert not result.ok
    assert "[pytest]" in result.report


def test_reports_type_errors(workdir):
    mistyped = "def slugify(text: str) -> int:\n    return text"
    runtime_passing = 'def test_identity():\n    assert slugify("x") == "x"'

    result = validate("slugify", mistyped, runtime_passing, workdir)

    assert not result.ok
    assert "[ty]" in result.report


def test_returns_formatted_source(workdir):
    ugly = 'def slugify( text:str )->str:\n    return  text.lower().replace(" ","-")'

    result = validate("slugify", ugly, TESTS, workdir)

    assert result.ok
    assert "def slugify(text: str) -> str:" in result.impl_source
