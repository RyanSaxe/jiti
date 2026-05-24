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

The first time `slugify` is called, jiti writes a real implementation into a `.jiti/`
companion file, validates it (ruff + ty + generated tests), commits it to disk, and runs
it. Every call after that runs that plain Python — fast, free, deterministic, and yours
to edit. It is a code generator, not a runtime LLM call: `@jiti` is closer to `protoc`
than to a chatbot.

## Why it's different

Most "AI function" libraries call the model on every invocation. jiti generates **real
code once** and then gets out of the way:

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

Write the class skeleton yourself and decorate only the methods you want generated. The
implementation may use `self`:

```python
class Invoice:
    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines

    @jiti
    def total(self) -> Decimal:
        """Sum the line totals, applying each line's discount."""
        ...
```

`invoice.total()` binds and type-checks like any method.

## How it works

- **One declaration → one translation unit.** Each `@jiti` function generates a
  self-contained implementation (one public symbol plus any private helpers) into a
  companion module that mirrors your source: `src/app/text.py` → `.jiti/app/text.py`,
  with tests under `.jiti/tests/`.
- **Repair loop.** jiti asks the model for an implementation and tests, runs ruff + ty +
  pytest, and feeds any failures back until everything is green.
- **You can edit generated code.** jiti tracks each section with a hash. Edit a body and
  it becomes yours — jiti runs it as-is and never overwrites it.
- **Changing the interface re-generates.** Change a stub's signature or docstring and
  jiti regenerates that section. If you've hand-edited it, jiti refuses to clobber your
  work and surfaces a conflict instead.

## Version control is yours

jiti **never runs git**. It only writes files into `.jiti/`. Commit that directory (so
production runs the cached code with no API key) or add it to `.gitignore` — your call.

## Configuration

By default jiti uses Anthropic's Claude and needs `ANTHROPIC_API_KEY` in the environment
during generation (never afterward). Point jiti at a different model, store, or any object
with a `complete(prompt) -> str` method:

```python
from jiti import Codegen, AnthropicModel, jiti
from jiti.store import JitiStore

codegen = Codegen(
    model_factory=lambda: AnthropicModel(model="claude-opus-4-7"),
    store=JitiStore(Path(".jiti")),
)


@jiti(strategy=codegen)
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    ...
```

## Status

Early MVP. Supported today: free functions and instance methods, lazy first-call
generation, the ruff + ty + pytest repair loop, the edit/conflict lifecycle, and the
Anthropic provider. Not yet: whole-class generation, a runtime `jiti.live` strategy,
multiple providers, and a `jiti eject` command that inlines generated code back into your
source and removes jiti entirely.
