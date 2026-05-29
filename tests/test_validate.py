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


def test_ty_catches_type_errors_in_the_test_code_too():
    """A test that calls the impl with a wrong-typed arg should fail statically — the agent
    shouldn't be tempted to "fix" the impl to accept the bad type, because the type bug is
    in the test."""
    impl = "def slugify(text: str) -> str:\n    return text"
    buggy_test = "def test_b():\n    assert slugify(123) == 'x'"  # 123 isn't str

    result = validate(impl, buggy_test, name="slugify")

    assert not result.ok
    assert "[ty-tests]" in result.report


def test_well_typed_tests_pass_ty_check():
    """A test that uses the impl correctly should not be flagged by the test-side ty check."""
    impl = "def slugify(text: str) -> str:\n    return text.lower()"
    good_test = "def test_g():\n    assert slugify('ABC') == 'abc'"

    result = validate(impl, good_test, name="slugify")

    assert result.ok


def test_runtime_contract_catches_return_lies_that_evade_static_checks():
    """An impl that uses `cast` to lie about its return type slips past ty but is caught
    at test-run time by the runtime contract wrap. This pins that the validation pipeline
    actually applies the wrap to the candidate before tests exec."""
    lying = (
        "from typing import cast\n\n"
        "def slugify(text: str) -> str:\n"
        "    return cast(str, 42)  # returns int despite `-> str`\n"
    )
    runtime_call = "def test_calls_it():\n    slugify('x')"

    result = validate(lying, runtime_call, name="slugify")

    assert not result.ok
    assert "[tests]" in result.report
    assert "ValidationError" in result.report or "validation" in result.report.lower()
