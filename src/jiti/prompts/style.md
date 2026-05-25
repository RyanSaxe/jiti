# jiti house style

Write code a careful reviewer would approve on the first pass.

## Naming
- Names reveal intent: `is_authenticated`, `total_price`, `parse_version` — not `x`, `tmp`.
- Verbs for functions, nouns for values; use one term per concept consistently.
- Avoid abbreviations except the universal ones (`id`, `url`, `html`, `api`).

## Types
- Type every parameter and return. Return types precise; inputs may be permissive.
- Prefer `X | None` over sentinels. Don't build alias chains the reader must chase.

## Control flow
- Guard clauses over nesting: return early on the invalid case, keep the happy path flat.
- Keep nesting <= 3 levels. Name a complex condition instead of inlining it.

## Validation & errors
- Validate only what's genuinely uncertain (missing data, out-of-range input). Trust types.
- Do NOT add defensive `isinstance`/`hasattr`/None checks the signature already guarantees.
- Fail fast: raise on misuse rather than silently returning a degenerate value.
- Catch exceptions only when you can handle them meaningfully.

## Structure
- Prefer plain functions; reach for a class only for real state or a protocol.
- Replace magic numbers with named constants.

## Comments
- Comment WHY, not WHAT. Self-documenting names beat narration.
- No comments that restate the code or the task.
