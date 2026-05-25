# jiti — Just In Time Implementation

> Declare a function by its **typed interface**; an LLM writes, tests, and caches a real
> implementation — committed Python you can read, edit, type-check, and own.

[![CI](https://github.com/ryansaxe/jiti/actions/workflows/ci.yml/badge.svg)](https://github.com/ryansaxe/jiti/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.13%2B-blue)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Types: ty](https://img.shields.io/badge/types-ty-261230)

```python
from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

The first time `slugify` is called, an in-process agent looks at the **real argument values**,
explores your codebase, and writes + tests a real implementation into a `.jiti/` companion file
(validated with ruff + ty + its own tests). It commits that to disk and runs it. Every call
after that runs plain Python — fast, free, deterministic, and yours to edit.

Unlike most "AI function" libraries, jiti does **not** call the model on every invocation. It
generates real code **once** and then gets out of the way.

## Contents

- [Why jiti](#why-jiti)
- [Installation](#installation)
- [The stub idiom](#the-stub-idiom)
- [Methods](#methods)
- [Test-driven generation](#test-driven-generation)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Version control is yours](#version-control-is-yours)
- [Concurrency](#concurrency)
- [Development](#development)
- [Status & roadmap](#status--roadmap)

## Why jiti

- **Agentic and live.** Generation runs at the call site, inspects the real inputs,
  experiments against copies, and explores the repo — so it writes code grounded in reality,
  not guesses.
- **Real, committed code.** The output is reviewable Python — diff it, edit it, step through it.
- **Typed.** It type-checks against your declared signature, so callers get full editor support.
- **No runtime dependency on the model.** After generation there is no model call, no API key,
  no latency, no nondeterminism.
- **Melds into existing code.** Decorate the functions you want written, hand-write the rest;
  jiti only ever touches the functions you decorate.

## Installation

Requires **Python 3.13+**. jiti is not on PyPI yet — install from source:

```bash
git clone https://github.com/ryansaxe/jiti && cd jiti
uv pip install -e .          # or: pip install -e .
```

Generation needs `ANTHROPIC_API_KEY` set; running already-generated (committed) code needs
nothing — no key, no network.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## The stub idiom

A jiti stub is a normal function whose body is just a docstring and a placeholder — use `...`:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    ...
```

Add comments as a hint, and jiti will use them:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    # strip currency symbols and thousands separators, then Decimal()
    ...
```

`pass` and `raise NotImplementedError` work as stub markers too. A body with real statements is
an error — `@jiti` means "generate this," so a function you've already written should not be
decorated.

> **Note on type checkers.** Strict checkers (ty, mypy) flag an empty body with a non-`None`
> return type (`empty-body`). That diagnostic is your checker reacting to your stub, not
> anything jiti does. Disable the `empty-body` rule, or use `raise NotImplementedError` as the
> body.

## Methods

Write the class skeleton yourself and decorate only the methods you want generated; the
implementation may use `self`:

```python
@dataclass
class Cart:
    prices: list[float]
    tax_rate: float

    @jiti
    def total(self) -> float:
        """Sum the prices and apply tax_rate, rounded to 2 decimals."""
        ...
```

`cart.total()` binds and type-checks like any method.

## Test-driven generation

Make a function's *definition of done* explicit: from your test file, declare the tests it must
pass with `@jiti.required_for(target)`. Tests import the code they test (so the reference is
real and type-checked), and **running `pytest` is the loop** — generation happens to satisfy
your tests, red → green.

```python
# tests/test_money.py
from decimal import Decimal

from app.money import parse_money
from jiti import jiti


@jiti.required_for(parse_money)        # real body → your own gate test
def test_parses_symbols():
    assert parse_money("$1,234.56") == Decimal("1234.56")


@jiti.required_for(parse_money)        # empty body → jiti writes this test from the interface
def test_parse_money_rejects_garbage() -> None:
    """parse_money raises ValueError on '' and 'not money'."""
    ...
```

- A **real-bodied** test is yours: it runs against the candidate during generation and must pass.
- An **empty-bodied** stub is a **jiti-test**. Because it's written *before* the implementation
  exists, it can only see the interface — so it can never couple to implementation details. jiti
  generates it (validated by ruff + ty), commits it under `.jiti/tests/`, and gates the
  implementation on it. Both are ordinary `test_*` functions your own `pytest` run executes.

Once a candidate is green, if the agent rates its own quality below `Engine(quality_threshold=…)`
(default 7) it takes one refactor pass (`max_refactor`, default 1) before committing. The
edge-case tests the agent writes for itself are committed as `test_scratch_*` — prune them when
you want a lean repo; your declared tests keep their names.

**How gates are found.** `required_for` registers a gate only when its test file is imported.
Under pytest, collection does that; but a plain call (your app, a script) wouldn't — so before
generating, jiti imports your test modules itself. By default it scans the working tree for
`test_*.py` / `*_test.py` (skipping `.jiti`, virtualenvs, caches); set
`Engine(test_paths=("tests",))` to narrow it (faster), or `Engine(test_paths=())` to turn it
off. This runs only when something actually needs generating.

## How it works

- **An in-process agent writes it.** At the call site, Claude gets tools to `inspect` the real
  values, `run_python` against deep-copied args, `read`/`grep` the codebase, and `submit`
  candidates (ruff + ty + tests, run in-process) — iterating until green.
- **Generation cascades along the call stack.** When a candidate's tests run and call another
  `@jiti` function, that callee is generated too — the live call graph *is* the dependency graph
  (cycles are detected and reported). Deep chains may hang while they build.
- **One declaration → one translation unit.** Each `@jiti` function becomes a self-contained
  implementation (one public symbol plus private helpers) in a companion module mirroring your
  source: `src/app/text.py` → `.jiti/app/text.py`, tests under `.jiti/tests/`.
- **You can edit generated code.** jiti tracks each section with a hash. Edit a body and it
  becomes yours — jiti runs it as-is and never overwrites it.
- **Changing the interface re-generates.** Change a stub's signature, docstring, or declared
  tests and jiti regenerates that section; if you've hand-edited it, jiti surfaces a conflict
  instead of clobbering your work.

## Configuration

By default jiti uses Anthropic's Claude and needs `ANTHROPIC_API_KEY` during generation (never
afterward — committed code runs without it). Supply your own engine — a custom Anthropic client,
model, store, or thresholds — with `@jiti(engine=...)`:

```python
from pathlib import Path

import anthropic
from jiti import Engine, JitiStore, jiti

engine = Engine(client=anthropic.Anthropic(), store=JitiStore(Path(".jiti")))


@jiti(engine=engine)
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

Clear the cache with `jiti.clear()` (or just delete `.jiti/`).

### Style & test guidance

jiti feeds two guides into the generation prompt so the code and tests it writes match your
conventions: a **style guide** (how code should read) and a **test guide** (how tests should be
written). Concise defaults ship with jiti; override either from whichever layer fits, highest
precedence first:

| Layer | Style guide | Test guide |
|---|---|---|
| Explicit argument | `Engine(style=...)` | `Engine(test_guide=...)` |
| Env var (a path) | `JITI_STYLE=/path/to/file.md` | `JITI_TESTS=/path/to/file.md` |
| Project file | `jiti.style.md` in the project root | `jiti.tests.md` in the project root |
| Bundled default | shipped with jiti | shipped with jiti |

Each guide is plain prose/Markdown — write it the way you'd brief a new teammate ("prefer guard
clauses"; "don't pile on near-duplicate test cases"). All prompt text lives in
`src/jiti/prompts/`.

### Logging

Generation is silent by default. Set `JITI_LOG=info` to see each LLM call (which function, the
cascade depth, the turn, duration, token usage, and an approximate cost), or `JITI_LOG=debug`
to also see the agent's tool calls. Logs go to stderr.

## Version control is yours

jiti **never runs git** — it only writes files into `.jiti/`. Commit that directory (so
production runs the cached code with no API key) or add it to `.gitignore`; your call.

## Concurrency

Two regimes, and the distinction is everything:

- **Running generated code is fully concurrency-safe.** Once a function is generated, calling it
  is plain in-memory dispatch — no shared state, no I/O, no locks. Call cached `@jiti` functions
  from as many threads or processes as you like.
- **Generating is not built for concurrent cold starts.** First-call generation does an LLM
  round-trip and writes the `.jiti/` companion; jiti does **no locking** there by design.

So the rule is simple: **warm the cache once, single-threaded, then parallelize.** Generate (run
the code or your test suite) on one process, commit `.jiti/`, and every later run — however
parallel — is a pure cache hit.

If several processes *do* cold-start the same module at once (e.g. `pytest -n auto` on a fresh
checkout), the one real hazard would be a torn companion file. jiti closes that: **every write is
atomic** (temp file + rename), so a reader always sees a complete file — old or new, never
half-written. The worst that remains is benign: a duplicate LLM call, or a section that
regenerates next run.

## Development

```bash
git clone https://github.com/ryansaxe/jiti && cd jiti
uv sync                      # install runtime + dev dependencies
uv run pre-commit install    # install the git hooks
```

The full gate — exactly what CI runs:

```bash
uv run pre-commit run --all-files   # ruff-format · ruff · ty
uv run pytest                       # the test suite (no API key needed; uses a fake client)
```

- **Pre-commit hooks** (`.pre-commit-config.yaml`): `ruff-format`, `ruff --fix`, and `ty` run on
  every commit. Fix the code rather than silencing a hook.
- **CI** (`.github/workflows/ci.yml`): on every push to `main` and every pull request, GitHub
  Actions runs the same pre-commit hooks and then `pytest` on Python 3.13.
- **Tooling**: dependencies via [uv](https://docs.astral.sh/uv/); format & lint with
  [ruff](https://docs.astral.sh/ruff/); type-check with [ty](https://github.com/astral-sh/ty);
  test with [pytest](https://docs.pytest.org/). jiti's own tests use a scripted fake client, so
  the suite is fast, offline, and deterministic.

A runnable example lives in `examples/semver/` — a semver toolkit declared as `@jiti` stubs with
a TDD spec under `examples/semver/tests/`.

## Status & roadmap

Early and evolving. **Supported today:** free functions and instance methods; lazy first-call
**agentic** generation (inspect real values, explore, experiment, test); in-process validation
with cascading generation; **test-driven generation** via `@jiti.required_for` (human gates +
interface-only jiti-tests) with a score-gated refactor pass; the edit/conflict lifecycle;
opt-in logging; Anthropic. Scoped to **pure functions**.

**Not yet:** a `jiti` CLI (`test prune`/`keep`, `merge`), a `jiti eject` command, `required_for`
on methods, an optional pytest plugin, whole-class generation, multiple providers, and
dependency-aware invalidation (changing a callee won't re-check its callers).
