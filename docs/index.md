# jiti

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

## Install

```bash
pip install jiti     # or: uv add jiti
```

Needs Python 3.13+. Generation uses LiteLLM and defaults to Claude Sonnet; set
`ANTHROPIC_API_KEY` for the default model, or set `JITI_MODEL` to any LiteLLM model id
and provide that provider's API key. Running already-generated code needs nothing — no
key, no network.

## A tiny example

```python
from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

The first call to `slugify("Hello, World!")` runs an agent that inspects the real
arguments, explores your repo, drafts code, runs ruff + ty + any tests you've gated on
it, and writes the result to a file beside your source. Every call after that runs that
file.

## Composing a graph

Interface-first pays off when interfaces compose. You write the orchestration in plain
Python — that's *your* code. jiti writes the leaves, and when one piece must go through
another, [`uses=`](guides.md#composition-contracts-uses) makes that a contract instead of
a hope:

```python
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

## Where to go next

- [Concepts](concepts.md) — the lifecycle: stubs, generation, the `.jiti/` mirror,
  edits and conflicts, graduation via `merge`.
- [Guides](guides.md) — test-driven generation, composition contracts, and freezing
  for production.
- [Reference](reference.md) — every `Engine` knob, environment variable, and CLI flag.
- [API](api.md) — the public surface, rendered from the docstrings.
- [`examples/semver/`](https://github.com/RyanSaxe/jiti/tree/main/examples/semver) — a
  runnable interface-first walkthrough.
