# jiti

[![CI](https://github.com/RyanSaxe/jiti/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanSaxe/jiti/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/jiti.svg)](https://pypi.org/project/jiti/)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Interface-first Python.** Declare the interfaces, wire them into a call graph, run the
program — an LLM writes the implementations the first time each one is called, and the
result is real, committable code that you keep.

## The idea

You decide *what*: typed signatures, docstrings, and tests. You decide *how the pieces
fit*: which function calls which. Then you run, and bodies appear just in time, get
validated against ruff + ty + your tests, and land as plain Python under `.jiti/`. Every
call after that is plain dispatch — no model, no API key, no network. When you're ready,
`jiti merge` folds the generated code back into your source and removes the decorator.

You can stop using jiti at any time and keep everything it wrote.

## A tiny example

```python
from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

The first call to `slugify("Hello, World!")` runs an agent that inspects the real
arguments, explores your repo, drafts code, runs ruff + ty + any tests you've gated on it,
and writes the result to a file beside your source. Every call after that runs that file.

## Wiring a graph

Interface-first pays off when interfaces compose. You write the orchestration in plain
Python — that's *your* code, and that's where the graph lives. jiti writes the leaves.

```python
from jiti import jiti


@jiti
def satisfies(version: str, spec: str) -> bool:
    """True if `version` satisfies `spec`. Specs: exact, '>=', '>', '<=', '<', '~', '^'."""
    ...


@jiti
def sort_versions(versions: list[str]) -> list[str]:
    """Return the version strings sorted ascending by semver precedence."""
    ...


# Your code — plain Python — composing the jiti pieces:
def latest_matching(versions: list[str], spec: str) -> str | None:
    """Return the highest-precedence version satisfying `spec`, or None."""
    candidates = [v for v in versions if satisfies(v, spec)]
    return sort_versions(candidates)[-1] if candidates else None
```

`latest_matching` is yours — no decorator, no magic, just a function. The first call to
`latest_matching(["1.0.0", "2.0.0", "2.1.3"], "^2.0.0")` runs your code, which calls
`satisfies` and `sort_versions`, which jiti generates on demand (and which may themselves
need other stubs along the way — generation cascades). Every call after that is plain
dispatch.

The full runnable version (with a `Version` dataclass, more stubs, and a method) lives in
[`examples/semver/`](examples/semver/).

## Install

```bash
pip install jiti     # or: uv add jiti
```

Needs Python 3.13+. Set `ANTHROPIC_API_KEY` to generate code. Running already-generated
code needs nothing — no key, no network.

## Stubs

A stub is a function with a docstring and a placeholder body: `...`, `pass`, or
`raise NotImplementedError`. A real body is an error — `@jiti` means "write this for me."
A comment in the stub becomes a hint:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    # strip the symbols and separators, then Decimal()
    ...
```

Methods work the same way — decorate the methods you want generated, use `self` freely
(see `Version.bump` in `examples/semver/core.py`).

> Strict type checkers flag an empty body with a non-`None` return (`empty-body`). That's
> your checker reacting to the stub, not jiti. Disable that rule or use `raise NotImplementedError`.

## Test-driven generation

State a function's definition of done from your test file with `@jiti.required_for(target)`.
Tests import the real code, so the reference is type-checked — and running `pytest` *is*
the loop: generation happens to make your tests pass, red → green.

```python
# tests/test_money.py
from app.money import parse_money
from jiti import jiti


@jiti.required_for(parse_money)        # real body → your gate test, run as-is
def test_parses_symbols():
    assert parse_money("$1,234.56") == Decimal("1234.56")


@jiti.required_for(parse_money)        # empty body → jiti writes this test from the interface
def test_rejects_garbage() -> None:
    """parse_money raises ValueError on '' and 'not money'."""
    ...
```

An empty-bodied stub is a **jiti-test**: written before the implementation exists, so it
can only see the interface and can't couple to internals. jiti writes it, commits it
under `.jiti/tests/`, and gates the implementation on it. Both are ordinary `test_*`
functions your own `pytest` run executes.

## Graduating off jiti

Interface-first is a *development mode* you can leave. `jiti merge` folds the generated
implementation back into your source, replaces the stub, removes `@jiti`, and cleans up
the mirror:

```bash
jiti status                  # what's generated, what you've hand-edited
jiti merge app.text.slugify  # inline one function into its source
jiti merge --all             # graduate the entire project
```

After `merge --all`, you have plain Python, no jiti dependency required. See
[`docs/reference.md`](docs/reference.md) for the full CLI and configuration surface.

## A few things worth knowing

- **The code is yours.** Edit a generated body and jiti runs it as-is — it tracks a hash
  and won't clobber your edits. Change a stub's signature, docstring, or gates and it
  regenerates; if you'd hand-edited that section, it surfaces a conflict instead.
- **git is yours.** jiti only writes files into `.jiti/`. Commit it (so production runs
  cached code with no key) or gitignore it. jiti never runs git.
- **Concurrency.** Running generated code is fully safe — it's plain dispatch. *Generating*
  does no locking, so warm the cache once single-threaded, then parallelize. Writes are
  atomic, so a reader never sees a half-written file.

## Where to go next

- [`examples/semver/`](examples/semver/) — a runnable interface-first walkthrough: stubs,
  a graph, tests, and the `merge` graduation.
- [`docs/reference.md`](docs/reference.md) — every `Engine` knob, env var, and CLI flag.
- [`CHANGELOG.md`](CHANGELOG.md) — release history.

## Development

```bash
uv sync
uv run pre-commit install
```

The gate, exactly what CI runs — ruff-format, ruff, ty, then pytest (no API key; uses a
fake client):

```bash
uv run pre-commit run --all-files
uv run pytest
```

## Status

Today, jiti supports free functions and methods, lazy agentic generation with cascading
across the call graph, in-process validation (ruff + ty + pytest), test-driven generation
via `@jiti.required_for` (works on free functions and methods), a score-gated refactor
pass, the edit/conflict lifecycle, and the `jiti` CLI (`status` / `merge` / `test` /
`clear`). Anthropic only.

Not yet: `merge` of methods that carry stacked decorators (`@classmethod`,
`@staticmethod`), a pytest plugin, whole-class generation, multiple model providers,
and dependency-aware invalidation.
