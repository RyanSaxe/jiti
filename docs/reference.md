# jiti reference

The flat reference for everything the [README](../README.md) doesn't spell out. For the
story and the workflow, start there.

## `Engine` configuration

Pass an `Engine` to `@jiti(engine=...)` to override defaults:

```python
from pathlib import Path

from jiti import Engine, JitiStore, jiti

@jiti(engine=Engine(store=JitiStore(Path(".jiti")), quality_threshold=8))
def slugify(text: str) -> str: ...
```

`Engine.__init__` parameters (see `src/jiti/agent/engine.py`):

| Parameter           | Default                  | Purpose                                                                 |
| ------------------- | ------------------------ | ----------------------------------------------------------------------- |
| `store`             | required                 | A `JitiStore` (the mirror at `.jiti/`).                                 |
| `model`             | `claude-sonnet-4-6`      | LiteLLM model id.                                                       |
| `max_tokens`        | `8192`                   | Per-call cap.                                                           |
| `max_turns`         | `40`                     | Agent loop limit before giving up.                                      |
| `style`             | packaged `STYLE_GUIDE`   | Prose style guide injected into the system prompt.                      |
| `test_guide`        | packaged `TEST_GUIDE`    | Prose test guide injected when generating tests.                        |
| `quality_threshold` | `7`                      | Minimum score to accept a candidate; lower triggers a refactor pass.    |
| `max_refactor`      | `1`                      | How many refactor passes to attempt before settling.                    |
| `test_paths`        | `None`                   | Where to find `@jiti.required_for` gates. `None` scans the working tree; a tuple narrows it; `()` disables discovery. |
| `execution_timeout` | `120.0`                  | Max **idle** seconds (no LLM call in flight) per in-process execution — a test, gate, or tool experiment. Cascaded generation resets the clock on every model call; only a hung candidate trips it. |
| `frozen`            | `False`                  | Refuse to generate. A cache miss raises `FrozenError` instead of calling the LLM. See [Frozen mode](#frozen-mode). |
| `completion`        | LiteLLM `completion`     | LiteLLM-compatible completion callable; tests can inject a fake.        |

## Models

jiti calls models through LiteLLM. The default is Anthropic Claude Sonnet 4.6, so set
`ANTHROPIC_API_KEY` for the default path:

```bash
ANTHROPIC_API_KEY=... python your_script.py
```

Set `JITI_MODEL` to any LiteLLM model id to use another model family. LiteLLM reads the
provider's usual environment variables:

```bash
OPENAI_API_KEY=... JITI_MODEL=openai/<model-id> python your_script.py
GEMINI_API_KEY=... JITI_MODEL=gemini/<model-id> python your_script.py
```

## Prose guide resolution

`style` and `test_guide` resolve in this order — first hit wins (see
`src/jiti/agent/prompts/__init__.py`):

1. Explicit `Engine(style=..., test_guide=...)`.
2. `JITI_STYLE` / `JITI_TESTS` env vars — paths to local markdown files.
3. `jiti.style.md` / `jiti.tests.md` in the project root.
4. Packaged defaults (`src/jiti/agent/prompts/style.md`, `tests.md`).

## Environment variables

| Variable            | Purpose                                                                  |
| ------------------- | ------------------------------------------------------------------------ |
| `ANTHROPIC_API_KEY` | Required to generate with the default model. Running cached code does not need it. |
| `JITI_MODEL`        | Model id for generation; defaults to `claude-sonnet-4-6`.                |
| `JITI_LOG`          | Log level — see [Logging](#logging). Unset means silent.                 |
| `JITI_STYLE`        | Path to a local style guide; overrides the packaged default.             |
| `JITI_TESTS`        | Path to a local test guide; overrides the packaged default.              |
| `JITI_FROZEN`       | When truthy (`1`/`true`/`yes`/`on`), the default engine refuses to generate. See [Frozen mode](#frozen-mode). |

## Logging

jiti is silent by default — its logger (`jiti`) attaches a `NullHandler` so downstream
users see nothing. Set `JITI_LOG` to opt in (see `src/jiti/core/log.py`):

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
jiti committed app.text.slugify — 6.4s (llm 5.9s across 2 calls) ~$0.1653
```

The committed line splits total wall time from time spent inside LLM calls, so a slow
generation is immediately attributable to the model or to validation.

Cost estimates are **labeled estimates, not billing-accurate**. jiti asks LiteLLM for
the model's configured completion cost when the provider response includes usage data,
then falls back to its local Claude price table for partial responses. Unknown models
log token counts without a dollar figure.

To route logs somewhere other than stderr, configure the `jiti` logger yourself before
the first `@jiti` call — `configure()` only attaches its `StreamHandler` if none is
already present:

```python
import logging
logging.getLogger("jiti").addHandler(my_handler)
```

## Stub forms

A `@jiti` function's body must be one of (see `src/jiti/core/declaration.py`):

- `...` (ellipsis)
- `pass`
- `raise NotImplementedError` (with or without a message)

A real body raises `RealBodyError`. Comments inside the stub are kept and shown to the
agent as hints.

`async def` stubs are supported. The decorated wrapper is marked as coroutine-like for
framework introspection, and the first async resolution runs in a worker thread so jiti's
blocking generation and validation do not nest inside the caller's active event loop.

> Strict type checkers (mypy, pyright, ty in strict mode) report `empty-body` for an
> empty function with a non-`None` return annotation. Disable that rule for stubs or use
> `raise NotImplementedError` instead.

## Composition contracts (`uses=`)

`@jiti(uses=[...])` declares symbols the generated implementation must use:

```python
@jiti(uses=[satisfies, sort_versions])
def latest_matching(versions: list[str], spec: str) -> str | None: ...
```

- Accepts **callables and classes** — pass the symbols themselves, not strings, so the
  reference is type-checked and refactor-safe. (Plain constants lose their names when
  passed; describe those in the docstring instead.)
- Each symbol's signature and docstring summary are injected into the task prompt as a
  MUST-use instruction.
- Validation adds a `uses` check: the candidate's AST must **reference** each symbol
  (bare name, `module.attr`, or an import alias). Reference — not invocation — so
  higher-order usage (`map(f, ...)`, `functools.partial(f, ...)`) counts. A candidate
  that never references a declared symbol fails with feedback the agent can act on.
- `uses=` joins the spec hash: editing the list regenerates the section.
- Other `@jiti` stubs are valid targets — generation cascades as usual.

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

Gating: merge refuses sections that have drifted from their source (regenerate first).
Decorators stacked above `@jiti` (`@staticmethod`, `@classmethod`, `@property`, cache
wrappers, etc.) are preserved while `@jiti` is removed. After implementations land, test
files are folded in — `@jiti.required_for` decorators are dropped from your user tests
(and stub bodies spliced in from the mirror), and scratch tests are appended to whichever
user test file already references the impl, or ejected into the project's test layout.

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

`JitiStore` tracks two hashes per section (see `src/jiti/core/store.py`):

- The **spec hash** — derived from the stub's signature, docstring, gates, and `uses=` list.
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

Every candidate goes through (see `src/jiti/core/validate.py`):

1. **ruff format** on the candidate file.
2. **ruff check** on the candidate file.
3. **ty check** on the candidate file.
4. **uses check** — every `@jiti(uses=[...])` symbol must be referenced (static AST scan).
5. **In-process tests** — `@jiti.required_for` gates plus the agent's own scratch tests,
   each bound against the candidate. Async tests and gates are awaited.

Each in-process execution (a test, a gate, a `run_python`/`inspect` tool call) is bounded
by `execution_timeout` — measured as **idle time**: the clock freezes while any LLM call
is in flight, so a test that legitimately cascades through nested generations runs as
long as it needs, while a runaway `while True:` candidate is abandoned and reported as a
failing check the agent can fix. (Python can't kill a thread, so the abandoned worker
leaks — bounded, and strictly better than hanging your process.)

If any step fails, the agent sees the failure and iterates. The agent gets up to
`max_turns` iterations and may submit multiple candidates; the first one to pass the
pipeline (and meet `quality_threshold`) wins.

When generating a test against a not-yet-implemented target, validation runs lint and
type-check only — no execution.

## Frozen mode

Frozen mode makes cache misses loud. With `Engine(frozen=True)` — or with
`JITI_FROZEN=1` for the default engine — any resolution that would trigger
generation (`GENERATE` or `REGENERATE`) raises `FrozenError` instead of calling
the LLM. Cached sections still run as plain dispatch.

The intended workflow:

1. **Develop unfrozen.** Generation runs, `.jiti/` fills up.
2. **Commit `.jiti/`** (or `jiti merge` it into source).
3. **Deploy with `JITI_FROZEN=1`.** Production runs only committed code — no key,
   no surprise latency, no surprise spend. A drift between source and `.jiti/`
   surfaces as a `FrozenError` on the call instead of a silent LLM round-trip.

`FrozenError` is a `JitiError`; catch it for a soft-fail path if you prefer.

## Concurrency

- **Running** generated code is fully safe — it's plain dispatch.
- **Generating** is serialized per function within a process: concurrent first calls
  (threads, or async tasks) share one generation — the losers block, then run the
  winner's committed code. Cascades on the same thread still re-enter normally, so
  genuine cycles raise `GenerationCycleError` rather than deadlocking.
- **Across processes** there is no locking (e.g. `pytest-xdist` workers can race to
  generate the same section). Warm the cache in one process first, then parallelize.
- **Writes** are atomic — a reader never sees a half-written file.

## Exceptions

| Exception              | When raised                                                          |
| ---------------------- | -------------------------------------------------------------------- |
| `JitiError`            | Base class for everything below.                                     |
| `GenerationError`      | The agent gave up — exceeded turns, or every candidate failed validation. |
| `GenerationCycleError` | A cascade tried to re-enter generation for a section already in progress. |
| `ConflictError`        | A hand-edited section's spec changed — see the lifecycle above.      |
| `FrozenError`          | Frozen mode is on and the section needs generating or regenerating.  |
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
