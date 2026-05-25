# jiti test guidance

Tests are the documentation of the function. Write the smallest, most diverse set that gives
real confidence and reads as worked examples.

## What to cover
- The core behavior on representative input — the happy path as a clear example.
- The edge cases that actually distinguish a correct implementation: boundaries, empties, and
  the error conditions named in the spec.
- One concept per test. Group several asserts only when they prove the same concept.

## What to avoid
- Don't restate the implementation. Test behavior through the public interface, never internals.
- Don't pile on near-duplicate cases — 1000 lines of tests for 50 lines of code is a failure,
  not thoroughness. If two cases exercise the same branch, keep the clearer one.
- Don't test the language or the type system (no `isinstance`/None checks the signature proves).
- Don't mock unless the dependency is external, slow, or non-deterministic.

## Naming
- Name each test for the behavior it pins: `test_rejects_leading_zero`, not `test_2`.
