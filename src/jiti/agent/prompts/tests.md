# jiti test guidance

Tests are the documentation of the function. Write the smallest, most diverse set that gives
real confidence and reads as worked examples.

## What to cover

- The core behavior on representative input — the happy path as a clear example.
- The edge cases that actually distinguish a correct implementation: boundaries, empties, and
  the error conditions named in the spec.
- One concept per test. Group several asserts only when they prove the same concept.
- Prefer shapes you observed via `inspect()` over invented ones. Tests double as documentation
  of how the function is actually called; real-shape data keeps them honest. Invented edge
  cases are fine *in addition to* a representative real-shape case, not instead of it.

## What to avoid

- Don't restate the implementation. Test behavior through the public interface, never internals.
- Don't pile on near-duplicate cases — 1000 lines of tests for 50 lines of code is a failure,
  not thoroughness. If two cases exercise the same branch, keep the clearer one.
- Don't test the language or the type system (no `isinstance`/None checks the signature proves).
- Don't mock unless the dependency is external, slow, or non-deterministic.
- Don't put imports inside test functions — keep them at the top of the file with the rest.

## Naming

- Name each test for the behavior it pins: `test_rejects_leading_zero`, not `test_2`.
