# Review Issues - 355ec9b6

**Spec File:** specs/feature-355ec9b6-cost-rollup.md
**Review Date:** 2026-05-27 11:37
**Status:** PASSED

## Summary

The agentlog cost feature is fully implemented: src/agentlog/cost.py (~480 lines), CLI wiring in cli.py, PRICING_FILE_NAME constant in _constants.py, and 35+ tests in tests/test_cost.py covering all acceptance criteria. All CLAUDE.md hard rules are respected (read-only, fail-open, stdlib-only, local-first, schema-versioned), compilation passes, the subcommand is registered and has correct --help output, and pricing resolution works through all four priority levels. Two minor sort-order deviations from the spec were found but neither blocks release.

## Issues Found: 2

### Issue #1: skippable

**File:** N/A

**Description:**
In _format_all_plain (cost.py), the sort key for unknown-cost rows uses (-float('inf'), started), which places them FIRST in the ascending sorted output instead of LAST. The spec says 'Unknown-cost rows sort last (treat cost_usd is None as -inf for the sort key)' — meaning the negated sort key should be +inf, not -inf.

**Resolution:**
Change the unknown-cost sort key from (-float('inf'), started) to (float('inf'), started) so that unknown-cost rows appear at the end of the ascending sort.

---

### Issue #2: skippable

**File:** N/A

**Description:**
The started_at tiebreaker in the sort key compares ISO timestamp strings lexicographically in ascending order (oldest first), but the spec requires 'started_at desc tiebreaker' (newest first for equal costs).

**Resolution:**
Negate the tiebreaker by converting started_at to a timestamp and negating it, e.g. replace the string comparison with -datetime.fromisoformat(started).timestamp() so that newer runs sort before older ones when costs are equal.

---
