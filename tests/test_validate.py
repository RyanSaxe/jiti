"""The validator: ruff + ty on the file, then the candidate's tests run in-process."""

from jiti.core.validate import validate

CORRECT = 'def slugify(text: str) -> str:\n    return text.lower().replace(" ", "-")'
TESTS = 'def test_basic():\n    assert slugify("Hello World") == "hello-world"'


def test_accepts_a_correct_implementation():
    result = validate(CORRECT, TESTS)

    assert result.ok
    assert result.report == ""


def test_reports_failing_tests():
    wrong = "def slugify(text: str) -> str:\n    return text.upper()"

    result = validate(wrong, TESTS)

    assert not result.ok
    assert "[tests]" in result.report


def test_reports_type_errors():
    mistyped = "def slugify(text: str) -> int:\n    return text"
    runtime_passing = 'def test_identity():\n    assert slugify("x") == "x"'

    result = validate(mistyped, runtime_passing)

    assert not result.ok
    assert "[ty]" in result.report


def test_returns_formatted_source():
    ugly = 'def slugify( text:str )->str:\n    return  text.lower().replace(" ","-")'

    result = validate(ugly, TESTS)

    assert result.ok
    assert "def slugify(text: str) -> str:" in result.impl_source
