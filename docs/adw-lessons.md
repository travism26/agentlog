# ADW lessons learned

Recurring patterns surfaced while building agentlog via the in-repo `.adw/` workflow. Each entry is a real polish-pass finding from one of the v0.1 SDLC runs, generalized into a rule the next iteration's research / plan / build / review phases should respect.

**Audience:** the `feature.md`, `implement.md`, `review.md` slash commands, and anyone running the ADW pipeline against this codebase. Read this file during the research and review phases.

**How to use:** if your work touches one of the patterns below, the rule applies. The "Why" line names the specific past incident; the "Test" line names the regression-test shape that proves the rule holds.

---

## 1. Any sort key MUST have a regression test asserting direction

**Why:** ADW step 5 (`cost`) shipped with two sign-flipped sort keys — unknown-cost rows landed at the top of the table (should have been the bottom), and equal-cost rows broke ties oldest-first (should have been newest-first). Both passed every other test because the suite verified content, not order. Reviewer caught them as "skippable."

**Rule:** for any function whose output order is meaningful, write a test that seeds two or more rows whose only meaningful difference is the sort-key dimension, then asserts the position of one relative to the other.

**Test shape:**
```python
def test_sort_puts_X_before_Y() -> None:
    # seed two rows that differ ONLY in the sort dimension
    ...
    pos_x = output.find("row-X")
    pos_y = output.find("row-Y")
    assert pos_x < pos_y, "expected X before Y under <sort_name>"
```

**Gotchas:**
- ISO-8601 timestamp strings compared as strings sort lexicographically — fine for ascending, broken for descending (because `-"2026-..."` doesn't work). Convert to UNIX seconds and negate.
- `None`/missing values: pick a sentinel (`float("inf")` or `float("-inf")`) and document which end the sentinel sorts to.

---

## 2. Module-level `assert` is a fail-open violation

**Why:** ADW step 2 (`capture.py`) had `assert set(_DISPATCH) == set(EVENTS)` at module-load time. Stripped under `python -O` (silent drift) AND would raise on every handler call if drift occurred — itself violating CLAUDE.md hard rule #2 (fail-open).

**Rule:** invariant checks belong in `tests/`, not in production module bodies. If you find yourself writing `assert <invariant>` at module scope, move it to a pytest with a clear name (`test_dispatch_table_matches_events`) and leave a comment at the original site explaining the invariant + pointing at the test.

**Counter-example to grep for:** `^assert ` at any indent level 0 (i.e., not inside a function or class) in `src/agentlog/*.py`.

---

## 3. SQLite schema-version bootstrap MUST run before incompatible-schema indexes

**Why:** ADW step 4 (`ls`) initially called `_init_schema` before `_check_schema_version`. When `INDEX_SCHEMA_VERSION` eventually bumps, the new `CREATE INDEX IF NOT EXISTS` against a renamed column would have raised before the version check got a chance to drop the old table.

**Rule:** any feature that introduces a SQLite (or other versioned-on-disk) artifact MUST structure its bootstrap as:
1. Create the version table (idempotent — its shape never changes; it IS the bootstrap)
2. Check version → drop incompatible main tables if mismatch
3. Create main tables + indexes (now guaranteed to be against the current schema)

**Test shape:** seed an index file, manually `UPDATE schema_version SET version = 999`, run the command, assert it recovers cleanly (table dropped + rebuilt, version reset).

---

## 4. Stale comments referencing "future" items rot fast

**Why:** ADW step 2 added a comment "ship-scope item #2 will replace with real handler logic" right above the code that *was* the implementation of ship-scope item #2. Reviewer caught it as skippable.

**Rule:** after each build phase, grep for comments referencing future work and re-read each one. If the future work is now done, the comment is wrong.

**Counter-example to grep for:** in changed files, `(future|will replace|TODO|FIXME|XXX|will land in|deferred to)` references that contradict the current diff.

---

## 5. Sentinel strings in installed files are part of the durable format

**Why:** `HOOK_COMMAND_PREFIX = "agentlog _hook"` ended up inside every user's `~/.claude/settings.json` after `agentlog init`. Renaming the constant orphans every installation: `uninstall` can't recognize old entries, `init` appends a parallel entry beside the old one.

**Rule:** any constant that is written to a file outside the agentlog source tree (settings.json, JSONL records, SQLite indexes other tools might inspect) is part of the *durable installed format*. Renaming it requires a migration plan, not a refactor. Document the constraint in a module-level docstring at the declaration site.

**Concrete examples in agentlog v0.1:** `HOOK_COMMAND_PREFIX`, `SCHEMA_VERSION` (in events.jsonl records), `INDEX_SCHEMA_VERSION` (in sqlite), `SOURCE_HOOKS` / `SOURCE_SDK` (string discriminators in JSON).

---

## 6. Reader/writer side effects (sort_keys, formatting) are user-visible

**Why:** `hooks_install.write_atomic` uses `sort_keys=True` for stable diffs across machines. The user's hand-curated key order in `settings.json` gets reordered on first `init`. Acceptable, but DOCUMENT it — never silently "preserve" because that introduces a worse failure mode (unstable diffs).

**Rule:** if your writer transforms the input (sort, normalize, reformat), say so in a module-level docstring. The user shouldn't discover it from a diff.

---

## 7. Fail-open boundaries must wrap EVERYTHING — including their own logging

**Why:** `capture.run_hook` catches `Exception` and logs to `_self.log`. But if `_log_self` itself raises (e.g., `~/.agentlog/` is read-only), the bare `except` would re-raise → break the user's Claude Code session.

**Rule:** in any fail-open boundary function, wrap the recovery / logging path in its own `with contextlib.suppress(Exception):` (or `try/except: pass`). The exit path of a fail-open function must be unconditional `return 0`.

**Test shape:** monkeypatch the recovery function itself to raise, verify the boundary still returns 0.

---

## 8. Distinguish stylistic reviewer items from spec violations

**Why:** ADW step 2's reviewer flagged `collections.abc.Callable` and `contextlib.suppress` as "outside the spec's allowed import list." Both are stdlib and ruff actively prefers them (UP035, SIM105). Applying the "fix" introduced lint errors.

**Rule:** before applying a reviewer item, check whether the change conflicts with the existing lint config. If `ruff check` would complain about the proposed fix, the reviewer was over-reading the spec — push back, leave a one-line note explaining why.

**Tooling:** every polish pass runs `ruff check src tests && mypy` as the final verification. If a reviewer item would break either, escalate rather than apply.

---

## 9. Long `if/elif rec_type == "..."` chains → dispatch dict

**Why:** ADW step 3's `_translate` shipped at cyclomatic complexity 31. The post-build polish split it into a `_RECORD_TRANSLATORS` dict + 5 small per-type helpers, dropping complexity to ~4 in the dispatcher and ≤8 in any helper. Same pattern as `capture._DISPATCH` from step 2.

**Rule:** if you find yourself writing more than ~3 `elif x == "literal":` branches in a row, hoist into a `dict[str, Callable]` dispatch table. Add a regression test that the table keys match the expected set of literals (see lesson #2 for how to structure that test).

**When NOT to:** if branches have distinct signatures or wildly different parameter sets, dispatch dict adds friction. Use judgment.

---

## 10. radon-complexity warnings are tech_debt, not bugs

**Why:** every ADW step since #2 has surfaced a few "function complexity > 10" warnings from the validate phase. None have ever indicated a real bug. They're a refactoring signal, classified correctly by the reviewer as tech_debt.

**Rule:** validate-phase complexity warnings are not blockers. They're worth addressing if (a) the function is over 20 and (b) the polish pass has spare cycles. Below 15, leave it — the threshold of 10 is conservative for a reason.

**Exception:** if a function is going to grow further in the next ship-scope item, splitting it preemptively pays off. See lesson #9 for the typical pattern.

---

## 11. Add a named regression test for every reviewer-confirmed bug

**Why:** the polish-pass fixes for steps 4 and 5 added regression tests with descriptive names (`test_cost_all_unknown_model_runs_sort_LAST_not_first`, etc.). Without these, a future refactor could silently re-break the behavior — exactly the regression problem the original review was trying to prevent.

**Rule:** when fixing a reviewer-confirmed bug, the same commit MUST include a test whose name describes the *contract being asserted* (not the implementation detail). The future contributor maintaining solo gets the same protection the reviewer provided.

**Naming pattern:** `test_<feature>_<scenario>_<observable_property>` — e.g., `test_uninstall_preserves_mixed_group_foreign_entries`.

---

## How this file gets used

- **Research phase** (`research.md`) — read this file as part of "Phase 1: Documentation Discovery." Anything that touches the patterns above already has context.
- **Plan phase** (`feature.md`) — when writing acceptance criteria, copy the relevant "Test shape" sections verbatim from any applicable lesson.
- **Review phase** (`review.md`) — the slash command should explicitly check for each pattern against the diff. See `.claude/commands/review.md` for the integration.
- **Polish pass** (this is the human-in-the-loop step) — if you find yourself addressing one of these patterns AGAIN, the lesson here didn't propagate. Strengthen the wording.

This file is itself the regression test for "did we learn anything." If you fix the same class of bug twice without updating it, that's a process bug worth surfacing.
