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
  implementation on it.

Once a candidate is green, if the agent rates its own quality below `Engine(quality_threshold=…)`
(default 7) it takes one refactor pass (`max_refactor`, default 1) before committing. The
edge-case tests the agent writes for itself are committed as `test_scratch_*` — prune them when
you want a lean repo; your declared tests keep their names.

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

### Style & test guidance

jiti feeds two guides into the generation prompt so the code and tests it writes match your
conventions: a **style guide** (how code should read) and a **test guide** (how tests should
be written). Concise defaults ship with jiti; override either from whichever layer fits,
highest precedence first:

| Layer | Style guide | Test guide |
|---|---|---|
| Explicit argument | `Engine(style=...)` | `Engine(test_guide=...)` |
| Env var (a path) | `JITI_STYLE=/path/to/file.md` | `JITI_TESTS=/path/to/file.md` |
| Project file | `jiti.style.md` in the project root | `jiti.tests.md` in the project root |
| Bundled default | shipped with jiti | shipped with jiti |

Each guide is plain prose/Markdown — write it the way you'd brief a new teammate ("prefer
guard clauses"; "don't pile on near-duplicate test cases"). All prompt text lives in
`src/jiti/prompts/` (`system.md`, `style.md`, `tests.md`).

## Concurrency

Two regimes, and the distinction is everything:

- **Running generated code is fully concurrency-safe.** Once a function is generated, calling
  it is plain in-memory dispatch — no shared state, no I/O, no locks. Call cached `@jiti`
  functions from as many threads or processes as you like.
- **Generating is not built for concurrent cold starts.** First-call generation does an LLM
  round-trip and writes the `.jiti/` companion; jiti does **no locking** there by design.

So the rule is simple: **warm the cache once, single-threaded, then parallelize.** Generate
(run the code or your test suite) on one process, commit `.jiti/`, and every later run —
however parallel — is a pure cache hit. It's the same committed-cache workflow that lets
production run without an API key.

If you *do* let several processes cold-start the same module at once (e.g. `pytest -n auto` on
a fresh checkout), the one real hazard would be a torn companion file. jiti closes that:
**every write is atomic** (temp file + rename), so a reader always sees a complete file — old
or new, never half-written. The worst that remains is benign: a duplicate LLM call, or a
section that simply regenerates next run. jiti deliberately stops here rather than taking on
cross-process locks, which add real complexity to avoid a cost this workflow already sidesteps.

## Status

Early and evolving. Supported today: free functions **and instance methods**, lazy
first-call **agentic** generation (inspect real values, explore, experiment, test),
in-process validation with cascading generation, **test-driven generation** via
`@jiti.required_for` (human gates + interface-only jiti-tests) with a score-gated refactor
pass, the edit/conflict lifecycle, and Anthropic. Scoped to **pure functions**. Not yet:
`required_for` on methods, a `jiti` CLI (`test prune`/`keep`, `merge`), an optional pytest
plugin, whole-class generation, multiple providers, dependency-aware invalidation (changing a
callee won't re-check callers), and a `jiti eject` command.
