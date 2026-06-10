# Concepts

The whole system is one loop: **declare → run → generate → validate → commit → own**.

## Stubs

A `@jiti` function is a *declaration*: a typed signature, a docstring, and a placeholder
body (`...`, `pass`, or `raise NotImplementedError`). A real body is an error — `@jiti`
means "write this for me." Comments inside the stub become generation hints:

```python
@jiti
def parse_money(raw: str) -> Decimal:
    """Parse a currency string like '$1,234.56' into a Decimal."""
    # strip the symbols and separators, then Decimal()
    ...
```

Methods, `async def`, and common decorator stacks (`@staticmethod`, `@classmethod`,
`@property`, `functools` wrappers) all work.

## Generation

On the first call, an in-process agent inspects the *live* arguments, explores your
repo, experiments against deep copies of the real data, and submits candidates until one
passes the full validation pipeline (ruff, ty, your gate tests, its own tests — see the
[validation pipeline](reference.md#validation-pipeline)). Generation **cascades**: if the
candidate's tests call other `@jiti` stubs, those generate too, depth-first, sharing one
engine and one cycle guard.

## The mirror

Committed code lands in `.jiti/`, a companion mirror of your source tree — one section
per declaration, each recording the spec it was generated for and a hash of its body.
That double hash powers the [edit/conflict lifecycle](reference.md#the-edit-conflict-lifecycle):

- Untouched section, same spec → **run** the cached code (no model, no key).
- Untouched section, changed spec → **regenerate**.
- Hand-edited section, same spec → **run your code as-is** — the code is yours.
- Hand-edited section, changed spec → **conflict**, surfaced loudly, never clobbered.

## Graduation

Interface-first is a development mode you can leave. `jiti merge` splices each generated
implementation back into your source at the stub site, drops the `@jiti` decorator,
folds the agent's tests into your test layout, and cleans up the mirror. After
`merge --all`, you have plain Python with no jiti dependency.

```bash
jiti status                  # what's generated, what you've hand-edited
jiti merge app.text.slugify  # inline one function
jiti merge --all             # graduate the entire project
```

## Trust boundaries

Worth knowing, plainly:

- Generated code executes **in-process** during validation and at dispatch. jiti is
  scoped to pure-function-shaped work; the agent's experiments run against deep copies
  of your arguments.
- During generation, argument *reprs* are sent to the model provider so the agent can
  see real shapes. Cached runs send nothing anywhere.
- A wall-clock guard bounds runaway candidates: in-process executions are abandoned
  after `execution_timeout` seconds of LLM-idle time — legitimate cascades keep the
  clock frozen, infinite loops trip it.
- For deployments, [frozen mode](guides.md#freezing-for-production) guarantees no LLM
  call can happen at runtime.
