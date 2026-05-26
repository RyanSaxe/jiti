# semver: an interface-first walkthrough

A small semver toolkit, defined entirely as interfaces, and implemented by jiti the first
time you run it. This is the long-form version of the [README example](../../README.md#wiring-a-graph).

## The graph

[`core.py`](core.py) declares the toolkit as a graph of `@jiti` stubs:

```
       latest ────▶ sort_versions ────▶ compare ────▶ parse
                                          ▲
                                          │
                                       satisfies
                                          │
                                          ▼
                                       Version
                                          │
                                          ▼
                                       Version.bump   ← a method stub
```

Every function is just a typed signature and a docstring. No bodies. The dependencies
between them — `latest` calls `sort_versions`, which calls `compare`, which calls `parse`
— are wired by *use*, not by configuration. When you call any node, jiti generates that
node, and any other nodes it ends up calling, in dependency order.

## The tests are the spec

[`tests/test_semver.py`](tests/test_semver.py) defines what each function has to do, with
`@jiti.required_for`. Some tests have real bodies (they ARE the gate); others are
empty-bodied stubs whose docstring is enough — jiti writes the test from the target's
interface.

```python
@jiti.required_for(parse)
def test_parse_reads_core_and_prerelease():
    assert parse("1.2.3") == Version(1, 2, 3)
    assert parse("1.2.3-rc.1") == Version(1, 2, 3, "rc.1")


@jiti.required_for(parse)
def test_parse_rejects_malformed() -> None:
    """parse raises ValueError on '1.2' (missing patch), '' (empty), and '01.2.3' (leading zero)."""
    ...
```

Methods are gated the same way — `Version.bump` has its own `@jiti.required_for` block.

## Generate the library

With `ANTHROPIC_API_KEY` set, either run the tests (red → green generates the bodies) or
run the demo CLI:

```bash
ANTHROPIC_API_KEY=… JITI_LOG=info pytest examples/semver/tests
# …or…
ANTHROPIC_API_KEY=… JITI_LOG=info python -m examples.semver demo
```

Afterward, look at what jiti wrote:

```bash
jiti status                       # shows every section + its tests
ls .jiti/examples/semver/         # the cached implementations live here
```

## Graduate off jiti

When you're happy with what's generated, fold it back into source:

```bash
jiti merge --all
```

The `@jiti` decorators come off, the implementations land in `core.py`, the generated
tests merge into `test_semver.py`, and the `.jiti/` mirror cleans up. You're left with
plain Python — no jiti dependency required. This is interface-first as a development
*mode* you can leave.
