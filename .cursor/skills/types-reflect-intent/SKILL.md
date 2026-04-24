---
name: types-reflect-intent
description: >-
  Aligns annotations and runtime code so types match business reality: narrow
  types, validate at boundaries, avoid defensive patterns that contradict
  non-optional types. Use when adding or changing type hints, refactoring
  Optional/None, reviewing PRs for honesty between types and guards, or when
  the user mentions strict typing, narrowing, or "types reflect intent."
---

# Types reflect intent

Types should match what the code actually guarantees. Narrow proactively; do not paper over gaps with defensive access patterns.

## Narrowing and boundaries

- If a value is always present after a certain point, make the type required—not `Optional` "just in case."
- If you reach for `None` checks, ask whether `None` is valid. If not, fix the type or data source upstream instead of guarding everywhere.
- Validate and normalize at **boundaries** (APIs, parsing, DB rows, config). Downstream code may assume validated shapes.
- Prefer literal unions (e.g. `'active' | 'inactive'`) over plain `str` when the set of values is known.

## Do not contradict types with defensive code

If the type says required, treat it as required:

- No `.get()`, optional chaining, `??`, or `.getattr()` for fields the type marks as always present.
- No extra null guards for values typed as non-nullable.
- No defaults that hide missing or invalid data (e.g. `or ''` when absence should fail loudly).

Loose types are a signal to **tighten types or fix producers**, not to add more defensive reads. Defensive code that masks bad data is worse than a clear failure at the boundary.

## Canonical reference

Project wording for this policy lives in `.cursor/rules/general_code_guidelines.mdc` under **Types Reflect Intent**. Prefer updating that rule if the policy changes; keep this skill aligned or link-only if it drifts.
