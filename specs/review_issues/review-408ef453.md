# Review Issues - 408ef453

**Spec File:** specs/feature-408ef453-docs-pages.md
**Review Date:** 2026-05-28 08:40
**Status:** PASSED

## Summary

Three documentation pages were created: docs/getting-started.md (238 lines), docs/cli-reference.md (392 lines), and docs/architecture.md (181 lines). All flag tables match live --help output flag-for-flag across all six subcommands. All acceptance criteria are met: PreToolUse absence documented with CLAUDE.md citation, timestamp-derivation paragraph is 4-6 sentences naming _RECORD_TRANSLATORS, all anchor links resolve to real headings, bb-token grep is clean, README.md and blog-draft.md are untouched. No blocking issues found.

## Issues Found: 2

### Issue #1: tech_debt

**File:** N/A

**Description:**
architecture.md line 56 incorrectly attributes cost.json finalization to `capture._on_session_end`. Inspecting src/agentlog/capture.py confirms _on_session_end only writes state.json via _write_state — there is no _write_cost call in that function. Only _on_stop and tail._process_one write cost.json. The 'finalise' attribution is factually wrong and will mislead contributors tracing the write path.

**Resolution:**
Update the 'Written by' column for cost.json in the runs/<id>/ layout table to remove 'capture._on_session_end (finalise)'. Correct entry: 'capture._on_stop (incremental); tail._process_one (one-shot)'.

---

### Issue #2: skippable

**File:** N/A

**Description:**
architecture.md is 181 lines, slightly below the 200-line lower bound stated in the spec. The spec explicitly calls this a sizing guideline with generous tolerance, and all required sections are present and complete.

**Resolution:**
Optional: expand the framing paragraph in 'Two ingest paths, one schema' or add depth to 'For contributors'. Not required for v0.1 release.

---
