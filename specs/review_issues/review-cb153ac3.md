# Review Issues - cb153ac3

**Spec File:** specs/feature-cb153ac3-view-tui.md
**Review Date:** 2026-05-27 13:38
**Status:** PASSED

## Summary

The agentlog view <id> three-panel TUI feature is fully implemented and matches the spec. src/agentlog/view.py is new and complete with correct rich gating, dispatch table, ANSI stripping, all four flags, and proper rc values. CLI wiring in cli.py is correct, _STUB_SUBCOMMANDS is frozenset(), and test_cli_smoke.py exclusion list includes view. All ADW lesson checks pass: Lesson #1 sort-order regression test exists, Lesson #2 no module-level asserts found, Lesson #4 no stale comments, Lesson #9 _TOOL_SUMMARIZERS dispatch table present with invariant test. Three test-coverage gaps exist for edge cases explicitly enumerated in the spec, all skippable.

## Issues Found: 3

### Issue #1: skippable

**File:** N/A

**Description:**
Spec unit-test list explicitly names test_view_default_summarizer_truncates_at_60_chars. That test is absent. The truncation is applied by _summarize_tool_use after calling any summarizer (view.py lines 241-243), but there is no test that exercises _default_summarizer specifically with a long params dict and cap=60, leaving the path untested by name.

**Resolution:**
Add test_view_default_summarizer_truncates_at_60_chars that calls _summarize_tool_use with tool='UnknownTool' and a params_summary containing a 200-char value, cap=60, and asserts the result ends with '…' and len <= 62.

---

### Issue #2: skippable

**File:** N/A

**Description:**
Spec edge-cases list includes 'cost.json with schema_version=2 — same handling'. _load_cost in view.py (lines 209-218) logs a warning and continues, mirroring _load_state, but there is no test covering this code path. The analogous test_view_state_json_schema_version_2_continues_rendering exists for state.json but not for cost.json.

**Resolution:**
Add test_view_cost_json_schema_version_2_continues_rendering that seeds a cost.json with schema_version: 2 and asserts rc == 0 and cost footer still renders.

---

### Issue #3: skippable

**File:** N/A

**Description:**
Spec edge-cases list includes 'Very long tool name — render without breaking column alignment; pick one and test.' The implementation pads via f'{tool!s:<{_TOOL_NAME_PAD}}' (view.py line 301) which left-aligns and overflows for names >8 chars without truncating, but no test verifies this chosen behavior or guards against a future change.

**Resolution:**
Add test_view_tool_use_long_tool_name_renders_without_error that seeds a tool_use event with tool='MultiFooBarBazQux' (>8 chars) and asserts rc == 0 and the tool name appears in output.

---
