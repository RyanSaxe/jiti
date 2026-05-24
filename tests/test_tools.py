"""The in-process tools: real-value inspection, copy-isolated experiments, validation."""

from jiti.declaration import introspect
from jiti.tools import CallContext


def double(items: list[int]) -> list[int]:
    """Double each item."""
    ...


def test_inspect_reads_the_real_value():
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    result = context.inspect("items")

    assert "list" in result
    assert "[1, 2, 3]" in result


def test_run_python_does_not_mutate_the_callers_args():
    original = [1, 2, 3]
    context = CallContext(introspect(double), (original,), {})

    context.run_python("items.append(99)")

    assert original == [1, 2, 3]


def test_submit_reports_failure_then_success():
    context = CallContext(introspect(double), ([1, 2],), {})
    tests = "def test_double():\n    assert double([1, 2]) == [2, 4]"

    failed = context.submit("def double(items):\n    return items", tests)
    assert failed.startswith("FAILED")
    assert context.passing is None

    passed = context.submit("def double(items):\n    return [i * 2 for i in items]", tests)
    assert passed.startswith("PASSED")
    assert context.passing is not None
