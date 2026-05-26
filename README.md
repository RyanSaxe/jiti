# jiti

[![CI](https://github.com/RyanSaxe/jiti/actions/workflows/ci.yml/badge.svg)](https://github.com/RyanSaxe/jiti/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Write the signature and the docstring. Let an agent write the body.

```python
from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

The first time `slugify` runs, an agent looks at the real arguments, explores your repo, and
writes an implementation into a `.jiti/` file beside your source — checked with ruff, ty, and
tests before it's saved. Every call after that is plain Python. No model, no API key, no network.

That's the whole point: jiti generates real, committable code **once** and gets out of the way.
It is not an "AI function" that calls a model on every invocation.

## Install

Needs Python 3.13+. Not on PyPI yet:

```bash
git clone https://github.com/RyanSaxe/jiti && cd jiti
uv pip install -e .
```

Set `ANTHROPIC_API_KEY` to generate code. Running already-generated code needs nothing.

## Stubs

A stub is a function with a docstring and a placeholder body — `...`, `pass`, or
`raise NotImplementedError`. A real body is an error: `@jiti` means "write this for me." Add a
comment and jiti treats it as a hint:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    # strip the symbols and separators, then Decimal()
    ...
```

Methods work too — write the class, decorate the methods you want generated, use `self` freely.

> Strict type checkers flag an empty body with a non-`None` return (`empty-body`). That's your
> checker reacting to the stub, not jiti. Disable that rule or use `raise NotImplementedError`.

## Test-driven generation

State a function's definition of done from your test file with `@jiti.required_for(target)`.
Tests import the real code, so the reference is type-checked — and running `pytest` *is* the
loop: generation happens to make your tests pass, red → green.

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

An empty-bodied stub is a **jiti-test**: written before the implementation exists, so it can
only see the interface and can't couple to internals. jiti writes it, commits it under
`.jiti/tests/`, and gates the implementation on it. Both are ordinary `test_*` functions your
own `pytest` run executes.

Gates only register when their test file is imported. pytest does that on collection; for
generation outside a test run, jiti imports your test modules first — scanning the tree by
default, or `Engine(test_paths=("tests",))` to narrow it.

## Configuration

Pass your own engine for a custom client, model, store, or thresholds:

```python
from jiti import Engine, jiti

@jiti(engine=Engine(quality_threshold=8))
def slugify(text: str) -> str: ...
```

Two prose guides shape what the agent writes — a **style guide** (how code reads) and a **test
guide** (how tests read). Defaults ship with jiti; override either with `Engine(style=...)` /
`Engine(test_guide=...)`, a `JITI_STYLE` / `JITI_TESTS` env path, or a `jiti.style.md` /
`jiti.tests.md` in your project root. All prompt text lives in `src/jiti/prompts/`.

`JITI_LOG=info` logs each model call; `JITI_LOG=debug` adds tool calls. `jiti.clear()` (or
`rm -rf .jiti`) drops the cache.

## The CLI

`jiti` ships a small command-line tool for inspecting and graduating generated code:

```bash
jiti status                  # what's generated, and what you've hand-edited (read-only)
jiti merge app.text.slugify  # inline one function into its source, drop @jiti
jiti merge --all             # …graduate the whole project off jiti
jiti test prune              # delete the agent's scratch tests (test_scratch_*)
jiti clear                   # delete .jiti/
```

`merge` is how you graduate: it folds the generated implementation back into your source file,
replacing the stub and removing `@jiti`, then cleans up the mirror — so `merge --all` is the
"I'm done with jiti" button (it leaves you plain Python and tells you when you can drop the
dependency). A target can be a file path, a dotted module, or a qualname; `--dry-run` previews.
Merge refuses sections that have drifted from their source (regenerate first) and, for now,
methods. `jiti test keep <name>` rescues a scratch test by un-prefixing it.

## A few things worth knowing

- **The code is yours.** Edit a generated body and jiti runs it as-is — it tracks a hash and
  won't clobber your edits. Change a stub's signature, docstring, or gates and it regenerates;
  if you'd hand-edited that section, it surfaces a conflict instead of overwriting.
- **git is yours.** jiti only writes files into `.jiti/`. Commit it (so production runs cached
  code with no key) or gitignore it. jiti never runs git.
- **Concurrency.** Running generated code is fully safe — it's plain dispatch. *Generating* does
  no locking, so warm the cache once single-threaded, then parallelize. Writes are atomic, so a
  reader never sees a half-written file.

## Development

```bash
uv sync
uv run pre-commit install
```

The gate, exactly what CI runs — ruff-format, ruff, ty, then pytest (no API key; uses a fake
client):

```bash
uv run pre-commit run --all-files
uv run pytest
```

`examples/semver/` is a runnable TDD spec: semver stubs with tests under
`examples/semver/tests/`. Run pytest there to watch jiti build the library.

## Status

Early. Today: free functions and methods, lazy agentic generation, in-process validation with
cascading generation, test-driven generation via `@jiti.required_for` with a score-gated
refactor pass, the edit/conflict lifecycle, the `jiti` CLI (`status`/`merge`/`test`/`clear`),
and Anthropic. Scoped to pure functions.

Not yet: `required_for` on methods, `merge` of methods, a pytest plugin, whole-class generation,
multiple providers, and dependency-aware invalidation.
