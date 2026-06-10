# Guides

The three workflows that turn jiti from a code generator into a development discipline.

## Test-driven generation

State a function's definition of done from your test file with
`@jiti.required_for(target)`. Tests import the real code, so the reference is
type-checked — and running `pytest` *is* the loop: generation happens to make your
tests pass, red → green.

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

Gates are part of the declaration's spec — add or edit one and the implementation
regenerates to satisfy it.

## Composition contracts (`uses=`)

When the correct implementation of one piece *must* go through another, say so with
`uses=` instead of hoping the docstring is persuasive:

```python
@jiti(uses=[satisfies, sort_versions])
def latest_matching(versions: list[str], spec: str) -> str | None:
    """Return the highest-precedence version satisfying `spec`, or None."""
    ...
```

You pass the symbols themselves (functions or classes — type-checked at the call site,
refactor-safe). The agent is told each one's signature and docstring as a MUST-use, and
validation statically rejects any candidate that never references them — so jiti can't
quietly re-implement `satisfies` inline and drift from your real one.

The check verifies *references*, not call sites: `map(satisfies, ...)` and
`functools.partial(satisfies, ...)` count. Changing the `uses=` list regenerates, just
like editing the docstring. Other `@jiti` stubs are valid targets — generation cascades
as usual.

## Freezing for production

Frozen mode makes cache misses loud. With `JITI_FROZEN=1` (or `Engine(frozen=True)`),
any resolution that would call the LLM raises `FrozenError` instead. Cached sections
still run as plain dispatch.

The intended workflow:

1. **Develop unfrozen.** Generation runs, `.jiti/` fills up.
2. **Commit `.jiti/`** (or `jiti merge` it into source).
3. **Deploy with `JITI_FROZEN=1`.** Production runs only committed code — no key, no
   surprise latency, no surprise spend. Drift between source and `.jiti/` surfaces as a
   `FrozenError` on the call instead of a silent LLM round-trip.

`FrozenError` is a `JitiError`; catch it for a soft-fail path if you prefer.

## Observability

Set `JITI_LOG=info` to watch generation live — per-turn timing, token usage, and an
approximate cost, with cascade depth shown as indentation:

```
jiti generating app.text.slugify
jiti app.text.slugify turn 1 — 3.2s in=4.1k out=0.8k cache_read=2.0k ~$0.0735
jiti app.text.slugify turn 2 — 2.7s in=4.8k out=1.1k cache_read=3.6k ~$0.0918
jiti committed app.text.slugify — 6.4s (llm 5.9s across 2 calls) ~$0.1653
```

Every generation also writes a transcript to `.jiti/transcripts/` — each turn and tool
call with inputs and results, kept **even on failure** — so "why did this cost that /
produce that / fail" is answerable after the fact without re-running.
