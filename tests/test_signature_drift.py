"""Signature drift detection: a hand-edit to `.jiti/` that breaks the contract fails loudly.

The spec-hash already invalidates when the *spec* changes; this catches the reverse — the
loaded impl's signature diverging from the spec, which would otherwise let wrong types
reach runtime far from the cause.
"""

import inspect

import pytest

from jiti.core.declaration import Declaration, compare_signatures
from jiti.core.errors import JitiError
from jiti.core.store import JitiStore


def _decl(signature: inspect.Signature) -> Declaration:
    return Declaration(
        module="app.core",
        qualname="run",
        name="run",
        signature=signature,
        docstring="doc",
        hint=None,
        available_symbols=(),
        class_context=None,
    )


def _signature(params: list[inspect.Parameter], ret: object = inspect.Signature.empty):
    return inspect.Signature(parameters=params, return_annotation=ret)


def _param(name: str, annotation: object = inspect.Parameter.empty) -> inspect.Parameter:
    return inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation)


# ---------- the comparator directly ----------


def test_matching_signature_returns_none():
    spec = _decl(_signature([_param("x", int)], int))

    def run(x: int) -> int:
        return x

    assert compare_signatures(spec, run) is None


def test_param_type_change_is_detected():
    spec = _decl(_signature([_param("x", int)], int))

    def run(x: str) -> int:
        return x  # ty: ignore[invalid-return-type]

    diff = compare_signatures(spec, run)

    assert diff is not None
    assert "x: int" in diff and "x: str" in diff


def test_optional_int_and_int_or_none_are_equivalent():
    """`Optional[int]` and `int | None` resolve identically through `eval_str=True`, so the
    comparator should not raise on the cosmetic difference."""
    from typing import Optional

    spec = _decl(_signature([_param("x", Optional[int])], int))  # noqa: UP045

    def run(x: int | None) -> int:
        return x or 0

    assert compare_signatures(spec, run) is None


def test_pep_695_type_alias_used_on_both_sides_does_not_drift():
    """A PEP 695 `type` alias used literally on both sides should compare equal — the alias
    object is the same identity, so element-wise annotation equality holds."""
    namespace: dict[str, object] = {}
    exec(
        compile(
            "type Vector = list[float]\ndef run(v: Vector) -> Vector:\n    return v\n",
            "<test>",
            "exec",
            dont_inherit=True,
        ),
        namespace,
    )
    loaded = namespace["run"]
    assert callable(loaded)
    spec = _decl(inspect.signature(loaded))

    assert compare_signatures(spec, loaded) is None


def test_future_annotations_in_loaded_impl_does_not_false_positive():
    """A hand-edit that adds `from __future__ import annotations` to a `.jiti` file keeps the
    annotations as strings. The comparator should still recognize them as equivalent."""
    spec = _decl(_signature([_param("x", int)], int))

    namespace: dict[str, object] = {}
    exec(
        compile(
            "from __future__ import annotations\n\ndef run(x: int) -> int:\n    return x\n",
            "<test>",
            "exec",
            dont_inherit=True,
        ),
        namespace,
    )
    loaded = namespace["run"]
    assert callable(loaded)

    assert compare_signatures(spec, loaded) is None


# ---------- store.load enforcement ----------


def test_store_load_raises_jiti_error_on_signature_drift(tmp_path):
    store = JitiStore(tmp_path / ".jiti")
    spec = _decl(_signature([_param("x", int)], int))
    store.write(
        spec,
        "def run(x: int) -> int:\n    return x",
        "def test_r():\n    assert run(1) == 1",
    )

    # Hand-edit the loaded impl to change the signature — simulates a user fiddling with
    # the `.jiti` file in a way that breaks the contract.
    path = store.impl_path(spec)
    path.write_text(path.read_text().replace("def run(x: int) -> int:", "def run(x: str) -> int:"))

    with pytest.raises(JitiError, match="Signature drift"):
        store.load(spec)


def test_store_load_succeeds_when_signatures_match(tmp_path):
    """A clean round-trip must not be tripped up by the drift check."""
    store = JitiStore(tmp_path / ".jiti")
    spec = _decl(_signature([_param("x", int)], int))
    store.write(
        spec,
        "def run(x: int) -> int:\n    return x * 2",
        "def test_r():\n    assert run(3) == 6",
    )

    loaded = store.load(spec)

    assert loaded(5) == 10
