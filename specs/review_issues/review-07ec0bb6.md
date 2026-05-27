# Review Issues - 07ec0bb6

**Spec File:** specs/feature-07ec0bb6-ls-unified-view.md
**Review Date:** 2026-05-27 09:46
**Status:** PASSED

## Summary

The agentlog ls feature is fully implemented and closely matches the spec. src/agentlog/ls.py exports run_ls with all required arguments; the SQLite caching layer, refresh-on-stale walker, three output formatters (plain/rich/JSON), and duration parser are all present and correct. CLI wiring in cli.py is complete with all flags wired to the correct defaults; tests/test_ls.py covers all 20+ acceptance-criteria cases plus additional integration and unit tests. No blocking issues found.

## Issues Found: 2

### Issue #1: skippable

**File:** N/A

**Description:**
`import re` is placed inside `_parse_duration` (src/agentlog/ls.py:258) rather than at the module top-level. This works correctly since stdlib re is always available, but deviates from standard Python convention and incurs a small repeated import lookup on every --since parse.

**Resolution:**
Move `import re` to the top-level imports block alongside the other stdlib imports.

---

### Issue #2: tech_debt

**File:** N/A

**Description:**
`_open_index` (src/agentlog/ls.py:117-122) calls `_init_schema(conn)` before `_check_schema_version(conn)`. For v0.1 this is harmless because there is no prior incompatible schema, but if a future INDEX_SCHEMA_VERSION=2 ships with renamed columns, the `CREATE INDEX IF NOT EXISTS` calls inside `_init_schema` could raise sqlite3.OperationalError against an old `runs` table before `_check_schema_version` ever gets a chance to drop and rebuild it — causing run_ls to return exit 1 instead of migrating gracefully.

**Resolution:**
Swap the call order in `_open_index`: call `_check_schema_version` first (it only reads from `schema_version`), then call `_init_schema`. Track as a v0.2 cleanup before bumping INDEX_SCHEMA_VERSION.

---
