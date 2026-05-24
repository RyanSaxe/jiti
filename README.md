# jiti — Just In Time Implementation

Declare a function or method by its **typed interface**; jiti has an LLM write a real,
validated, cached implementation that you can read, edit, type-check, and own.

```python
from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

The first time `slugify` is called, an in-process agent looks at the **real argument
values**, explores your codebase, and writes + tests a real implementation into a `.jiti/`
companion file (validated with ruff + ty + its own tests). It commits that to disk and runs
it. Every call after that runs plain Python — fast, free, deterministic, and yours to edit.

## Why it's different

Most "AI function" libraries call the model on every invocation. jiti generates **real
code once** and then gets out of the way:

- Generation is **agentic and live**: it runs at the call site, inspects the real inputs,
  experiments, and explores the repo — so it writes code grounded in reality, not guesses.
- The generated code is committed, reviewable Python — diff it, edit it, step through it.
- It type-checks against your declared signature, so callers get full editor support.
- After generation there is no model, no API key, no latency, no nondeterminism.
- It melds into existing code: decorate the functions you want written, hand-write the
  rest. jiti only ever touches the functions you decorate.

## The stub idiom

A jiti stub is a normal function whose body is just a docstring and a placeholder. Use
`...`:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    ...
```

You can add comments as a hint, and jiti will use them:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    # strip currency symbols and thousands separators, then Decimal()
    ...
```

`pass` and `raise NotImplementedError` work as stub markers too. A body with real
statements is an error — `@jiti` means "generate this," so a function you've already
written should not be decorated.

> **Note on type checkers:** strict type checkers (ty, mypy) flag an empty body with a
> non-`None` return type (`empty-body`). That diagnostic is your checker reacting to your
> stub, not anything jiti does. If it bothers you, disable the `empty-body` rule or use
> `raise NotImplementedError` as the body.

## How it works

- **An in-process agent writes it.** At the call site, Claude gets tools to `inspect` the
  real values, `run_python` against deep-copied args, `read`/`grep` the codebase, and
  `submit` candidates (ruff + ty + tests, run in-process) — iterating until green.
- **Generation cascades along the call stack.** When a candidate's tests run and call
  another `@jiti` function, that callee is generated too — the live call graph *is* the
  dependency graph (cycles are detected and reported). Deep chains may hang while they build.
- **One declaration → one translation unit.** Each `@jiti` function becomes a
  self-contained implementation (one public symbol plus private helpers) in a companion
  module mirroring your source: `src/app/text.py` → `.jiti/app/text.py`, tests under
  `.jiti/tests/`.
- **You can edit generated code.** jiti tracks each section with a hash. Edit a body and it
  becomes yours — jiti runs it as-is and never overwrites it.
- **Changing the interface re-generates.** Change a stub's signature or docstring and jiti
  regenerates that section; if you've hand-edited it, jiti surfaces a conflict instead of
  clobbering your work.

## Version control is yours

jiti **never runs git**. It only writes files into `.jiti/`. Commit that directory (so
production runs the cached code with no API key) or add it to `.gitignore` — your call.

## Configuration

By default jiti uses Anthropic's Claude and needs `ANTHROPIC_API_KEY` set during generation
(never afterward — committed code runs without it). Supply your own engine — a custom
Anthropic client, model, or store — with `@jiti(engine=...)`:

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

## Status

Early and evolving. Supported today: free functions, lazy first-call **agentic** generation
(inspect real values, explore, experiment, test), in-process validation with cascading
generation, the edit/conflict lifecycle, and Anthropic. Scoped to **pure functions**. Not
yet: method generation (the next step), whole-class generation, multiple providers,
dependency-aware invalidation, and a `jiti eject` command.
