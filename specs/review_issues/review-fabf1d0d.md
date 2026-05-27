# Review Issues - fabf1d0d

**Spec File:** specs/feature-fabf1d0d-hook-handlers-capture.md
**Review Date:** 2026-05-27 08:30
**Status:** PASSED

## Summary

The hook handler capture implementation is complete and correct. All five event recorders (SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd) are implemented in src/agentlog/capture.py, the _constants.py extraction is clean with proper re-exports in hooks_install.py, the CLI wire-up replaces the lambda stub with _run_hook, and the test suite covers all specified cases including the full lifecycle integration test. All CLAUDE.md hard rules are satisfied: fail-open boundary, no network calls, stdlib-only dependencies, atomic file writes, and schema_version:1 on every record.

## Issues Found: 4

### Issue #1: skippable

**File:** N/A

**Description:**
Stale comment in cli.py:57-58 — 'Hidden no-op stub — ship-scope item #2 will replace with real handler logic.' Item #2 is now implemented so this is no longer a stub.

**Resolution:**
Update the comment to reflect the real handler is wired in, e.g., 'Routes agentlog _hook <Event> to capture.run_hook; kept hidden from --help.'

---

### Issue #2: tech_debt

**File:** N/A

**Description:**
capture.py imports contextlib and collections.abc.Callable, which are not in the explicitly allowed import list in the spec Notes section. Both are stdlib so pyproject.toml stays clean, but the deviation from the stated constraint is noteworthy.

**Resolution:**
Replace contextlib.suppress with an inline try/except Exception: pass (matching the spec's own fail-open boundary example). Replace collections.abc.Callable with typing.Callable.

---

### Issue #3: tech_debt

**File:** N/A

**Description:**
_read_json in capture.py silently swallows json.JSONDecodeError without logging to _self.log. The spec (Phase 2, Step 3) explicitly requires logging the JSONDecodeError case. This means corrupt cost.json or state.json files produce no diagnostic trace.

**Resolution:**
Add a root: Path parameter to _read_json and call _log_self(root, f'malformed JSON in {path}: {exc}') in the JSONDecodeError branch. Update callers in _on_stop and _on_session_end to pass root.

---

### Issue #4: tech_debt

**File:** N/A

**Description:**
assert set(_DISPATCH) == set(EVENTS) at capture.py:364 fires at module import time. With -O optimization it is silently stripped; if the dispatch table ever diverges, every agentlog _hook invocation raises AssertionError — violating the fail-open spirit of CLAUDE.md rule #2.

**Resolution:**
Move this invariant into a pytest test (e.g., test_dispatch_table_matches_events) rather than a module-level assert.

---
