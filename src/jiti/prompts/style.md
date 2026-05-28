# jiti house style

Write code a careful reviewer would approve on the first pass.

## Naming

- Names reveal intent: `is_authenticated`, `total_price`, `parse_version` — not `x`, `tmp`.
- Verbs for functions, nouns for values; use one term per concept consistently.
- Avoid abbreviations except the universal ones (`id`, `url`, `html`, `api`, etc.).

## Types

- Type every parameter and return. Return types precise; inputs may be permissive.
- Prefer `X | None` over sentinels. Don't build alias chains the reader must chase.
- Avoid `Any` or `object` unless absolutely necessary.

## Control flow

- Guard clauses over nesting: return early on the invalid case, keep the happy path flat.
- Keep nesting <= 3 levels unless absolutely necessary. Name a complex condition instead of inlining it.

## Validation & errors

- Validate only what's genuinely uncertain (missing data, out-of-range input). Trust types.
- Do NOT add defensive `isinstance`/`hasattr`/None checks the signature already guarantees.
- Fail fast: raise on misuse rather than silently returning a degenerate value.
- Catch exceptions only when you can handle them meaningfully.
- Raise the specific built-in that fits (`ValueError` for bad values, `TypeError` for wrong types, `KeyError`/`IndexError` for lookups). Error messages should name the offending value so it shows up in the traceback — not just "invalid input."

## Structure

- Prefer plain functions; reach for a class only for real state or a protocol.
- Replace magic numbers with named constants.
- Do not mutate caller arguments. If you need to transform a list/dict/set passed in, copy first; the caller still holds the original.
- Helpers should be small and only exist if they meaningfully reduce duplication or clarify intent. A `_<name>__helper` called from a single site and inline-able without loss of clarity should be inlined.

## Comments

- Comment WHY, not WHAT. Self-documenting names beat narration.
- No comments that restate the code or the task.
