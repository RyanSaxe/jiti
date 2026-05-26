# jiti reference

The flat reference for everything the [README](../README.md) doesn't spell out. For the
story and the workflow, start there.

## `Engine` configuration

Pass an `Engine` to `@jiti(engine=...)` to override defaults:

```python
from jiti import Engine, jiti

@jiti(engine=Engine(quality_threshold=8))
def slugify(text: str) -> str: ...
```

`Engine.__init__` parameters (see `src/jiti/engine.py`):

| Parameter           | Default                  | Purpose                                                                 |
| ------------------- | ------------------------ | ----------------------------------------------------------------------- |
| `client`            | required                 | An `anthropic.Anthropic`-like object; only `.messages.create(...)` is used. |
| `store`             | required                 | A `JitiStore` (the mirror at `.jiti/`).                                 |
| `model`             | `claude-opus-4-7`        | Model id passed to `client.messages.create`.                            |
| `max_tokens`        | `8192`                   | Per-call cap.                                                           |
| `max_turns`         | `40`                     | Agent loop limit before giving up.                                      |
| `style`             | packaged `STYLE_GUIDE`   | Prose style guide injected into the system prompt.                      |
| `test_guide`        | packaged `TEST_GUIDE`    | Prose test guide injected when generating tests.                        |
| `quality_threshold` | `7`                      | Minimum score to accept a candidate; lower triggers a refactor pass.    |
| `max_refactor`      | `1`                      | How many refactor passes to attempt before settling.                    |
| `test_paths`        | `None`                   | Where to find `@jiti.required_for` gates. `None` scans the working tree; a tuple narrows it; `()` disables discovery. |

## Prose guide resolution

`style` and `test_guide` resolve in this order — first hit wins (see
`src/jiti/prompts/__init__.py`):

1. Explicit `Engine(style=..., test_guide=...)`.
2. `JITI_STYLE` / `JITI_TESTS` env vars — paths to local markdown files.
3. `jiti.style.md` / `jiti.tests.md` in the project root.
4. Packaged defaults (`src/jiti/prompts/style.md`, `tests.md`).

## Environment variables

| Variable            | Purpose                                                                  |
| ------------------- | ------------------------------------------------------------------------ |
| `ANTHROPIC_API_KEY` | Required to generate. Running cached code does not need it.              |
| `JITI_LOG`          | Log level — see [Logging](#logging). Unset means silent.                 |
| `JITI_STYLE`        | Path to a local style guide; overrides the packaged default.             |
| `JITI_TESTS`        | Path to a local test guide; overrides the packaged default.              |

## Logging

jiti is silent by default — its logger (`jiti`) attaches a `NullHandler` so downstream
users see nothing. Set `JITI_LOG` to opt in (see `src/jiti/_log.py`):

| Value                 | Effect                                                         |
| --------------------- | -------------------------------------------------------------- |
| unset                 | Silent.                                                        |
| `info` / `1` / `true` / `yes` | One line when generation starts, one per LLM turn, one when it commits. |
| `debug`               | Adds per-tool-call detail inside the agent loop.               |

Output goes to stderr, formatted as `jiti <message>`. Each line carries the function key,
cascade depth (shown as indentation), and for LLM-call lines the turn number, wall time,
token usage, and an approximate USD cost:

```
jiti generating app.text.slugify
jiti app.text.slugify turn 1 — 3.2s in=4.1k out=0.8k cache_read=2.0k ~$0.0735
jiti app.text.slugify turn 2 — 2.7s in=4.8k out=1.1k cache_read=3.6k ~$0.0918
jiti committed app.text.slugify — 5.9s ~$0.1653
```

Cost estimates are **labeled estimates, not billing-accurate**, and only appear for
models with known pricing (currently `claude-opus-4-7`, `claude-sonnet-4-6`,
`claude-haiku-4-5`). Unknown models log token counts without a dollar figure.

To route logs somewhere other than stderr, configure the `jiti` logger yourself before
the first `@jiti` call — `configure()` only attaches its `StreamHandler` if none is
already present:

```python
import logging
logging.getLogger("jiti").addHandler(my_handler)
```

## Stub forms

A `@jiti` function's body must be one of (see `src/jiti/declaration.py`):

- `...` (ellipsis)
- `pass`
- `raise NotImplementedError` (with or without a message)

A real body raises `RealBodyError`. Comments inside the stub are kept and shown to the
agent as hints.

> Strict type checkers (mypy, pyright, ty in strict mode) report `empty-body` for an
> empty function with a non-`None` return annotation. Disable that rule for stubs or use
> `raise NotImplementedError` instead.

## CLI

`jiti` ships a single command. `--root <path>` overrides the project root (defaults to
the current working directory).

### `jiti status`

Print every generated section and its state. Read-only — does not import your code.

### `jiti merge [targets...] [--all] [--dry-run] [--prune]`

Fold generated implementations back into your source files, replacing each stub and
removing the `@jiti` decorator.

- `targets` — a file path, a dotted module (`app.text`), or a qualname (`app.text.slugify`).
  Methods use `Class.method`.
- `--all` — merge every generated section.
- `--dry-run` — print the plan; write nothing.
- `--prune` — drop the agent's scratch tests instead of appending them to a user test file.

Gating: merge refuses sections that have drifted from their source (regenerate first)
and methods that carry stacked decorators (`@classmethod`, `@staticmethod`). After
implementations land, test files are folded in — `@jiti.required_for` decorators are
dropped from your user tests (and stub bodies spliced in from the mirror), and scratch
tests are appended to whichever user test file already references the impl, or ejected
into the project's test layout.

### `jiti test prune [--dry-run]`

Delete the agent's scratch tests (`test_scratch_*`) from the mirror. `--dry-run` reports
the count without writing.

### `jiti test keep <name>`

Promote a scratch test by dropping its `scratch_` prefix so `prune` won't drop it.
Note: regenerating the section drops it again — for a durable test, move it into your
own test suite or wrap it with `@jiti.required_for`.

### `jiti clear`

Delete the entire `.jiti/` mirror.

## The edit / conflict lifecycle

`JitiStore` tracks two hashes per section (see `src/jiti/store.py`):

- The **spec hash** — derived from the stub's signature, docstring, and gates.
- The **content hash** — derived from the implementation body as written.

When `@jiti` is called, the store resolves the section to one of five actions:

| Action       | Meaning                                                                           |
| ------------ | --------------------------------------------------------------------------------- |
| `GENERATE`   | No cached section exists — run the agent.                                         |
| `RUN`        | Cached section exists, spec matches, body unedited — execute it.                  |
| `REGENERATE` | Cached body unedited but spec changed — re-run the agent.                         |
| `RUN_OWNED`  | Body was hand-edited and the spec still matches — execute the user's code as-is.  |
| `CONFLICT`   | Body was hand-edited *and* the spec has since changed — refuse, raise `ConflictError`. |

You resolve a conflict by either reverting your edits or updating the stub to match the
edited body.

## Validation pipeline

Every candidate goes through (see `src/jiti/validate.py`):

1. **ruff format** on the candidate file.
2. **ruff check** on the candidate file.
3. **ty check** on the candidate file.
4. **In-process tests** — `@jiti.required_for` gates plus the agent's own scratch tests,
   each bound against the candidate.

If any step fails, the agent sees the failure and iterates. The agent gets up to
`max_turns` iterations and may submit multiple candidates; the first one to pass the
pipeline (and meet `quality_threshold`) wins.

When generating a test against a not-yet-implemented target, validation runs lint and
type-check only — no execution.

## Concurrency

- **Running** generated code is fully safe — it's plain dispatch.
- **Generating** does no locking. Warm the cache once single-threaded, then parallelize.
- **Writes** are atomic — a reader never sees a half-written file.

## Exceptions

| Exception              | When raised                                                          |
| ---------------------- | -------------------------------------------------------------------- |
| `JitiError`            | Base class for everything below.                                     |
| `GenerationError`      | The agent gave up — exceeded turns, or every candidate failed validation. |
| `GenerationCycleError` | A cascade tried to re-enter generation for a section already in progress. |
| `ConflictError`        | A hand-edited section's spec changed — see the lifecycle above.      |
| `RealBodyError`        | A `@jiti` function has a non-stub body.                              |

## Test discovery

`@jiti.required_for` gates only register when their test file is imported. pytest does
that on collection. For generation triggered outside a test run (e.g. running your app
locally), jiti imports your test modules first:

- `Engine(test_paths=None)` (default) — walk the working tree.
- `Engine(test_paths=("tests",))` — narrow to specific dirs/files (faster).
- `Engine(test_paths=())` — disable discovery (use when you don't have `required_for` gates).

`@jiti.required_for(target)` accepts both free functions and methods as the target (see
`Version.bump` in `examples/semver/tests/test_semver.py`). The *test* you decorate must
itself be a plain function (a method-style test raises at decoration time).
