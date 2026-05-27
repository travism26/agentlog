# Review Issues - 1b4319ab

**Spec File:** specs/feature-1b4319ab-init-uninstall-hooks.md
**Review Date:** 2026-05-27 06:07
**Status:** PASSED

## Summary

The agentlog init/uninstall feature is fully implemented across hooks_install.py, cli.py, and the test suite. All acceptance criteria are met: idempotency, foreign-hook preservation, atomic writes, PreToolUse exclusion, hidden _hook no-op subparser, and malformed-JSON protection with clean non-zero exit. No blocking issues were found.

## Issues Found: 2

### Issue #1: tech_debt

**File:** N/A

**Description:**
The implementation suppresses `_hook` from argparse help output by mutating `sub._choices_actions`, a private attribute of `argparse._SubParsersAction`. Functional on Python 3.11 but not part of the public API and could break on future CPython versions.

**Resolution:**
Accept as-is for v0.1 given the Python 3.11+ only constraint and the fact the cli.py comment already documents the rationale. Track for cleanup if a Python upgrade breaks it.

---

### Issue #2: skippable

**File:** N/A

**Description:**
The spec's Edge Cases section explicitly calls for a test where a single group's `hooks` list contains both agentlog and foreign entries mixed together. The implementation handles this correctly, but no test exercises this topology — existing tests use two separate groups (one all-foreign, one all-agentlog).

**Resolution:**
Add a test seeding a single group with `"hooks": [{"command": "agentlog _hook PostToolUse"}, {"command": "other-tool"}]` and asserting only `other-tool` survives after plan_uninstall. Low risk to defer past v0.1 since the production code is correct.

---
