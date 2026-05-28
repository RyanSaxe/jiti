"""The in-process tools: real-value inspection, copy-isolated experiments, validation."""

import pytest

from jiti import tools
from jiti.declaration import introspect
from jiti.errors import JitiError
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


def test_grep_finds_matches_in_python_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text("def needle():\n    pass\n")
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    result = context.grep("def needle")

    assert "sample.py" in result
    assert "def needle" in result


def test_grep_returns_no_matches_when_pattern_is_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text("def haystack():\n    pass\n")
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    assert context.grep("nonexistent_symbol") == "(no matches)"


def test_grep_raises_with_install_hint_when_rg_missing(monkeypatch):
    monkeypatch.setattr(tools, "_AVAILABLE_CLIS", {})
    monkeypatch.setenv("PATH", "/nonexistent")
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    with pytest.raises(JitiError) as info:
        context.grep("anything")

    assert "ripgrep" in str(info.value)
    assert "brew install ripgrep" in str(info.value)


def test_sg_finds_structural_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.py").write_text("def alpha():\n    pass\n\ndef beta(x):\n    return x\n")
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    result = context.sg("def $NAME($$$): $$$")

    assert "alpha" in result
    assert "beta" in result


def test_sg_raises_with_install_hint_when_sg_missing(monkeypatch):
    monkeypatch.setattr(tools, "_AVAILABLE_CLIS", {})
    monkeypatch.setenv("PATH", "/nonexistent")
    context = CallContext(introspect(double), ([1, 2, 3],), {})

    with pytest.raises(JitiError) as info:
        context.sg("anything")

    assert "ast-grep" in str(info.value)
    assert "brew install ast-grep" in str(info.value)
