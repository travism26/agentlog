# Research: `agentlog ls` — unified-view listing across hooks + SDK runs

## Metadata

adw_id: `07ec0bb6`
prompt: `/tmp/agentlog_step4_prompt.md` — implement v0.1 ship-scope item #4: `agentlog ls`, the first read-time tool, listing every captured run across both data sources with a lazy SQLite cache index over the canonical `runs/<id>/state.json` + `cost.json`.
date: `2026-05-27`

## Executive Summary

`ls` is the **first read-time tool** in the project — it's the artifact that pays for the capture infrastructure installed by `init`/`_hook`/`tail`. Implementation lands in a brand-new `src/agentlog/ls.py` plus one inline subparser branch in `src/agentlog/cli.py` (pulling `"ls"` out of `_STUB_SUBCOMMANDS`). The SQLite index at `$AGENTLOG_HOME/index.sqlite3` is a *cache*, never the source of truth — the canonical data remains the per-run JSON files written by `capture.py` and `tail.py`, which `ls` only reads. No changes are required to `capture.py`, `tail.py`, or `hooks_install.py`; the new module is purely additive and read-only with respect to `runs/`.

## Existing Architecture

### Relevant Documentation Found

| Path | Purpose for this task |
|---|---|
| `DESIGN.md` (lines 87-131) | Data-flow diagram — both sources produce identical `runs/<id>/{state,events,cost}.json`. `ls` is the unified surface over both. |
| `DESIGN.md` ship-scope table (line 193) | "`agentlog ls` (unified view) — 1 day — SQLite index across both sources." Confirms scope, schedule, and index pattern. |
| `DESIGN.md` lines 61-71 | Use cases #1, #4 — `ls --since 8h --sort cost` and conversation-history scrolling. Both depend on `ls` existing as the front door. |
| `CLAUDE.md` hard rules #6 (local-first / no network), #7 (`schema_version: 1` versioning + drop-and-rebuild on mismatch), #8 (idempotence + preservation pattern from `init` — mirrored here for the index file) | Non-negotiable constraints `ls` must respect. Rule #2 (fail-open) is hook-only; `ls` is a user CLI so it can fail-loud on user error. |
| `ai_docs/research/0241d756-tail-sdk-sidecar-analysis.md` | Prior research doc — establishes the pattern that "fail-loud" CLIs duplicate the ~10 helper functions (`_data_root`, `_log_self`, `_isoformat`) rather than couple to `capture.py`. `ls` follows the same convention. |
| `ai_docs/research/fabf1d0d-hook-handlers-capture-analysis.md` | Hooks research — documents what `state.json` and `cost.json` actually contain (the fields `ls` reads). |
| `ai_docs/research/1b4319ab-init-uninstall-cli-analysis.md` | Init research — pattern for adding a new top-level CLI subcommand (the cli.py refactor mirrors that step). |

### Component Map

```
                ┌──────────────────────────────────────────────┐
                │  $AGENTLOG_HOME/runs/                        │
                │   ├─ <session-id>/    (hooks-mode, written   │
                │   │   ├─ state.json     by capture.py)       │
                │   │   ├─ events.jsonl                        │
                │   │   └─ cost.json                           │
                │   └─ sdk-<session-id>/  (SDK-mode, written   │
                │       ├─ state.json     by tail.py)          │
                │       ├─ events.jsonl                        │
                │       └─ cost.json                           │
                └──────────────────────┬───────────────────────┘
                                       │   (READ-ONLY for ls)
                                       │   mtime-fingerprinted
                                       ▼
                ┌──────────────────────────────────────────────┐
                │  src/agentlog/ls.py (NEW)                    │
                │    refresh_index(conn, runs_root)            │
                │    query_runs(conn, filters, sort)           │
                │    format_table(rows)  /  format_json(rows)  │
                │    run_ls(args) -> int                       │
                └──────────────────────┬───────────────────────┘
                                       │     reads + writes
                                       ▼
                ┌──────────────────────────────────────────────┐
                │  $AGENTLOG_HOME/index.sqlite3                │
                │    runs table  (cache; canonical = JSON)     │
                │    schema_version table  (=1)                │
                └──────────────────────────────────────────────┘

                CLI wiring:
                  src/agentlog/cli.py
                    _STUB_SUBCOMMANDS -= {"ls"}
                    new `elif name == "ls":` branch w/ argparse args
                    _run_ls(args) -> ls.run_ls(...)
```

### Key Files and Modules

| File | Why it matters for `ls` |
|---|---|
| `src/agentlog/cli.py:12-14` | `SUBCOMMANDS = ("init", "uninstall", "tail", "ls", "cost", "view")` already lists `ls`. `_STUB_SUBCOMMANDS = frozenset({"ls", "cost", "view"})` — remove `"ls"`. |
| `src/agentlog/cli.py:25-87` | Existing `for name in SUBCOMMANDS:` loop — add an `elif name == "ls":` branch mirroring the `tail` shape (lines 57-87): create subparser, add all flags, `sp.set_defaults(func=_run_ls)`. Add a `_run_ls(args)` helper below `_run_tail` (lines 102-109). |
| `src/agentlog/_constants.py` | Add `INDEX_FILE_NAME = "index.sqlite3"` and `INDEX_SCHEMA_VERSION = 1`. The existing `SCHEMA_VERSION = 1` (line 28) refers to the JSONL wire schema and must remain distinct from the index schema (they may drift independently). `DEFAULT_DATA_ROOT_NAME` and `RUNS_DIR_NAME` are already present and reusable. `SOURCE_HOOKS`/`SOURCE_SDK` already exported — `ls` filters use these. |
| `src/agentlog/capture.py:38-122` | Source of helpers (`_data_root`, `_log_self`, `_session_dir`) the prompt explicitly allows duplicating into `ls.py`. The prompt's note ("reuse `capture._log_self` … or write a tiny equivalent here to keep ls.py independent") matches the existing tail.py pattern. Do NOT import `capture` runtime — keeps `ls` independent of hook-path code. |
| `src/agentlog/capture.py:130-156` | Documents what hooks-mode `state.json` contains: `schema_version, session_id, parent_session_id, started_at, ended_at, cwd, model, event_count, source, summary`. `ls` must read every one of these as missing-tolerant. |
| `src/agentlog/capture.py:264-281` | Documents hooks-mode `cost.json` shape: `{schema_version, session_id, totals: {input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}, phases: {}}`. The four token kinds named here are the exact ones `ls` sums for the `TOKENS` column and the `total_tokens` index column. |
| `src/agentlog/tail.py:345-360` | Documents SDK-mode `state.json` — *superset* of hooks-mode (extra: `source_file, source_name, session_failed, truncated`). All fields `ls` needs are present in both shapes; the extra fields can be ignored. |
| `src/agentlog/tail.py:443-452` | Documents SDK-mode `cost.json` — identical shape to hooks-mode. Good news: the parser is uniform. |
| `pyproject.toml:28` | `dependencies = []` MUST stay empty. `sqlite3` is stdlib — no addition needed. `rich>=13.7` already gated under `[project.optional-dependencies] tui` (line 31). |
| `pyproject.toml:80-84` | mypy strict on `src` and `tests`. Means: `ls.py` types every function signature; `sqlite3.Row` rows need `dict()` or careful typing; `Optional[...]` for nullable columns. |
| `pyproject.toml:64-78` | ruff: line-length 100, selects `E/W/F/I/B/UP/SIM`. Existing files use modern `from __future__ import annotations` + PEP 604 unions (`str | None`). Match this style. |
| `tests/test_cli_smoke.py:25-32` | Parametrised "not yet implemented" test currently excludes `{"init", "uninstall", "tail"}`. Must extend to include `"ls"` (i.e., become `{"init", "uninstall", "tail", "ls"}`) or this test fails the moment `ls` stops printing "not yet implemented". |
| `tests/test_tail.py:23-44` | Pattern reference for new `tests/test_ls.py` — `AGENTLOG_HOME` env monkeypatched to `tmp_path`, fixture builders for JSON state, JSONL events. |
| `tests/fixtures/sdk_minimal.jsonl` | Existing SDK fixture. New `ls` tests will likely seed run-dirs by calling `tail.run_tail` against this fixture rather than constructing state.json by hand — already the pattern in `test_tail.py`. |

### Schema fields actually written today

State.json (hooks-mode, from `capture._on_session_start` and `_on_session_end`):

```
schema_version, session_id, parent_session_id, started_at, ended_at,
cwd, model, event_count, source ("hooks"), summary
```

State.json (SDK-mode, from `tail._process_one`):

```
schema_version, session_id, source ("sdk"), source_file, source_name,
started_at, ended_at, cwd, model, event_count, session_failed,
truncated, summary, parent_session_id
```

Cost.json (BOTH sources):

```
schema_version, session_id,
totals: { input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens },
phases: {}
```

`ls` reads ONLY the intersection (`schema_version, session_id, source, parent_session_id, started_at, ended_at, cwd, model, event_count` from state; the four tokens from cost.totals). All other fields are ignored — extras tolerated.

## Affected Areas

### Files That Will Need Changes

| File | Change |
|---|---|
| `src/agentlog/ls.py` | **NEW**. Public surface: `run_ls(...) -> int`. Internals: SQLite open/init/migrate, refresh-on-stale walker, `_index_run`, query builder for filters + sort + limit, table formatter (plain), `rich` formatter (gated by `try import rich` AND `sys.stdout.isatty()`), JSON formatter, `--since` duration parser. ~350-450 lines including types and helpers. |
| `src/agentlog/cli.py` | Remove `"ls"` from `_STUB_SUBCOMMANDS` (line 14). Add new `elif name == "ls":` branch in the build-parser loop (insert near line 87, after the `tail` branch). Add `_run_ls(args)` helper below `_run_tail` (line ~109). Import: `from agentlog import ..., ls`. |
| `src/agentlog/_constants.py` | Add `INDEX_FILE_NAME: str = "index.sqlite3"` and `INDEX_SCHEMA_VERSION: int = 1` near the existing `SCHEMA_VERSION` constant. Keep both versions distinct (JSONL schema vs. index schema). |
| `tests/test_cli_smoke.py:25` | Extend the exclusion set from `{"init", "uninstall", "tail"}` to `{"init", "uninstall", "tail", "ls"}` so the "not yet implemented" parametrised test no longer covers `ls`. |
| `tests/test_ls.py` | **NEW**. Acceptance-criteria coverage: empty-runs-dir, populated dir (seeded via `tail.run_tail` against `tests/fixtures/sdk_minimal.jsonl`), `--sort tokens --reverse`, `--source sdk`/`--source hooks`, `--since 1h`/`--since 7d`/`--since garbage`, `--json` (parse and assert structure), `--reindex`, `--limit 3`, idempotency (second `ls` invocation does not rebuild stale rows when nothing changed — verifiable by spying on `_index_run`), missing runs dir, malformed state.json (verify skipped + stderr warning), `cost.json` missing (verify `total_tokens=0`), schema version drift (write a row with version=999, verify rebuild). ~15-20 test functions. |

### Files explicitly NOT changing

- `src/agentlog/capture.py` — `ls` is read-only over `runs/`; hook-path code is untouched.
- `src/agentlog/tail.py` — same; `ls` is downstream of `tail`'s outputs.
- `src/agentlog/hooks_install.py` — unrelated to read-side tooling.
- `pyproject.toml` — `dependencies = []` stays empty (`sqlite3` is stdlib). `[tui]` extra (`rich>=13.7`) already declared; no edits required.
- `README.md` — explicitly out of scope per prompt; docs phase handles `docs/feature-07ec0bb6-ls-unified-view.md`.

### Dependencies

**Inbound** (what depends on the new module): only `cli.py`. The module is leaf-positioned in the import graph.

**Outbound** (what `ls.py` depends on):
- stdlib: `argparse, json, os, re, sqlite3, sys, contextlib, datetime, pathlib, typing` (and `time` for mtime).
- `agentlog._constants` — for `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SELF_LOG_NAME`, `SOURCE_HOOKS`, `SOURCE_SDK`, plus the new `INDEX_FILE_NAME` and `INDEX_SCHEMA_VERSION`.
- Optional, lazy-imported under `try/except ImportError`: `rich.table.Table`, `rich.console.Console`. Both gated by `sys.stdout.isatty()` so pipes/CI fall back to plain output even when `rich` is installed.

### Integration Points

1. **Index file location**: `_data_root() / INDEX_FILE_NAME` — i.e., `~/.agentlog/index.sqlite3` (default) or `$AGENTLOG_HOME/index.sqlite3`. This sits alongside `runs/`, `_self.log` — same root that `capture._data_root()` and `tail._data_root()` resolve to. Critical that all three modules agree (they all read `os.environ["AGENTLOG_HOME"]` with the same fallback).
2. **Runs walk**: `_data_root() / RUNS_DIR_NAME` iterated with `Path.iterdir()`. Each entry that is a directory containing a readable `state.json` becomes one indexed row. Entries without `state.json` are silently skipped (mid-write or crashed run).
3. **mtime fingerprinting**: per-run staleness check uses `state.json.stat().st_mtime` + `cost.json.stat().st_mtime` (0.0 sentinel when cost.json absent). These are the **only** files we stat on the steady-state path. Crucially, `events.jsonl` is NOT opened by `ls` — that's the file that can grow to megabytes and would dominate runtime if scanned.
4. **`_self.log`**: malformed-state-json warnings append to `_data_root() / SELF_LOG_NAME` via a local copy of `_log_self`. Shared log file with `capture` and `tail`, but no shared writer.

## Impact Analysis

### Scope of Change

Small-to-medium. One new module (~400 lines), one new test file (~300 lines), 4 lines of edits to `cli.py`, 2 lines to `_constants.py`, 1 line to `test_cli_smoke.py`. No public API changes outside the new subcommand surface. No on-disk format changes to `runs/<id>/*` — the index is additive.

The change is **strictly additive**: nothing existing is rewritten. If `ls.py` were deleted tomorrow, the rest of the system would continue functioning (capture + tail still produce the canonical JSON files; only the read-side query tool disappears).

### Risks and Considerations

1. **Index drift vs. source-of-truth.** The cache could in principle disagree with the JSON files. Mitigation: re-stat every run on every `ls` invocation; cheap (a few hundred stats is sub-millisecond on modern SSDs); detect mismatch by comparing stored `state_mtime`/`cost_mtime` to current file mtimes; only re-read JSON on mismatch. `--reindex` is the operator-controlled "nuke and rebuild" escape hatch.
2. **Concurrent `ls` invocations.** Two parallel `agentlog ls` runs could both attempt to refresh the same row. SQLite handles last-writer-wins via its file locking. Worst case is wasted work; correctness is preserved. Documented as a known limitation, not a bug.
3. **Schema version drift.** If a future version writes index schema 2 and the user downgrades to ls-v0.1, version mismatch must be handled. The prompt's prescription: drop the `runs` table and rebuild (preserving other tables a future feature might add). This is the simplest forward-compatible behavior and matches the "schema versioned, fail-tolerant" pattern from CLAUDE.md rule #7.
4. **`rich` gating.** The optional-extra pattern is well-established in the project (only `[tui]` extras), but `ls.py` must gate `rich` on BOTH (a) successful import AND (b) `sys.stdout.isatty()`. A user with `rich` installed but piping output should get plain output. The TTY check matters for the `agentlog ls | grep ...` case explicitly called out by the prompt as a hard requirement.
5. **Duration parser correctness.** `--since 1h` style needs a tiny regex parser. Edge cases: `1.5h` (decimal — reject for v0.1 simplicity), uppercase `7D` (case-insensitive accept), zero/negative (`0h`, `-1h` — reject), trailing whitespace. Should exit 2 with a clear stderr message on parse failure, NOT crash. Argparse `type=...` callable is the right place — it raises `ArgumentTypeError` which argparse formats correctly.
6. **Argparse `choices=`.** `--sort` accepts `started, ended, duration, events, tokens, cost`. Note `cost` is interpreted as `total_tokens` ordering until item #5 lands — document in the help text (or coerce `cost` to `tokens` in v0.1 with a deprecation note; recommend the latter for forward-compat).
7. **`--source` mutual exclusion.** Prompt says mutually exclusive — argparse `choices=["hooks", "sdk", "all"]` with `default="all"` handles this trivially (single-value enum, not a flag set).
8. **Unicode in run IDs.** Today's IDs are UUIDs and `sdk-` + UUID prefixes — ASCII only. Defensive: cast to `str()` when formatting, never assume ASCII width, but don't over-engineer. The acceptance criteria doesn't require pathological-id support.
9. **First-run case.** `~/.agentlog/` may not exist (no `init` run yet, no `tail` run yet). Print to stdout `"no runs found at <path>"`, exit 0. The index file should NOT be created in this case (preserves the "writes nothing if there's nothing to do" invariant).
10. **mtime resolution on different filesystems.** APFS gives nanosecond precision; ext4 default is millisecond; some network filesystems round to seconds. `state_mtime` stored as REAL (float). Equality comparison (`row["state_mtime"] != s_mt`) is safe within a single filesystem; cross-filesystem moves of `~/.agentlog/` could cause spurious re-indexing. Acceptable for v0.1 — `--reindex` is the workaround.

### Existing Patterns to Follow

| Pattern | Where it shows up | How `ls` uses it |
|---|---|---|
| `from __future__ import annotations` at the top of every module | All four `src/agentlog/*.py` files | Same at top of `ls.py`. |
| `_data_root()` reads `AGENTLOG_HOME` env, falls back to `Path.home() / DEFAULT_DATA_ROOT_NAME` | `capture.py:38-42`, `tail.py:52-56` | Duplicate verbatim in `ls.py` (per the "fail-loud CLIs duplicate helpers" convention from tail). |
| `_log_self(root, message)` — best-effort append to `_self.log`, never raises | `capture.py:74-82`, `tail.py:77-85` | Duplicate verbatim for malformed-state-json warnings. |
| `_isoformat(dt)` — `dt.astimezone(UTC).isoformat(timespec="microseconds")` | `capture.py:49-50`, `tail.py:63-64` | For `indexed_at` column. For the **display** column, prompt says drop microseconds (`timespec="seconds"` + ensure `Z` suffix). |
| `argparse` subparser registered via `for name in SUBCOMMANDS:` loop with `elif name == "ls":` branch and `sp.set_defaults(func=_run_ls)` | `cli.py:25-87` (init, uninstall, tail branches) | Same shape exactly. |
| `_run_<command>(args: argparse.Namespace) -> int` thin shim → module's `run_<command>(...)` typed public function | `cli.py:102-126` | Same shape: `_run_ls(args)` → `ls.run_ls(...)`. |
| Public `run_<command>(...)` returns `int` (rc 0/1/2 with documented meanings) | `tail.py:462-509`, `hooks_install.run_init/run_uninstall` | Same. rc 0 = success or empty-tree (normal); 1 = unexpected I/O / DB failure; 2 = user error (bad `--since`, missing path). |
| Stdlib-only public surface; optional `rich` gated by `[tui]` extra and try/except | Discussed in `_constants.py` notes and `DESIGN.md` "Stack" section | `try: import rich.table` inside formatter, fall through to plain on ImportError. |
| Module docstring opens with one-paragraph summary + numbered notes on invariants | `capture.py:1-9`, `tail.py:1-23`, `hooks_install.py:1-21`, `_constants.py:1-12` | Same. Pin: (a) "index is a cache, never the source of truth"; (b) "read-only with respect to runs/"; (c) "fail-loud — `ls` is a user CLI, not a hook hot-path"; (d) "schema versioned: drop+rebuild on mismatch". |
| Helper functions module-private (`_leading_underscore`); only `run_*` exported | All modules | Same in `ls.py`. |
| Tests under `tests/test_<module>.py`, monkeypatch `AGENTLOG_HOME` → `tmp_path` | `tests/test_capture.py`, `tests/test_tail.py` | Same shape for `tests/test_ls.py`. |
| Seed test data by calling actual production functions (`tail.run_tail` against fixtures) rather than hand-writing JSON | `tests/test_tail.py` heavily; `tests/test_capture.py` calls `capture.dispatch` directly | Use `tail.run_tail` with `tests/fixtures/sdk_minimal.jsonl` to seed SDK runs; call `capture.dispatch` directly to seed hooks-mode runs. |

## Recommendations

### Implementation order

1. **`_constants.py` first.** Add `INDEX_FILE_NAME` and `INDEX_SCHEMA_VERSION`. Single commit; trivial.
2. **`ls.py` skeleton.** Module docstring with the four pinned invariants. Helpers duplicated from `tail.py` (`_data_root`, `_log_self`, `_isoformat`). Empty `run_ls(...)` returning 0.
3. **SQLite layer.** Connection open with `Path` → `str`. `_init_schema(conn)` creates tables + indexes (per prompt SQL) and INSERT-OR-IGNOREs the version row. `_check_schema_version(conn)` reads the version; on mismatch DROP `runs` + the `schema_version` row, re-create. Use `sqlite3.Row` factory.
4. **Refresh walker.** `_refresh_index(conn, runs_root)` per the prompt's algorithm. Skip dirs without `state.json`. Compare mtimes; only re-read JSON for stale runs. Purge deleted runs at the end. Single commit.
5. **`_index_run`.** Read state.json + cost.json (use a local `_read_json` that mirrors `capture._read_json` — tolerant of missing files and JSONDecodeError; logs malformed JSON to `_self.log` and skips the row with a stderr warning). UPSERT into `runs`.
6. **Query layer.** Build a SQL SELECT with WHERE clauses constructed from validated args. Bind parameters (never string-interpolate user input — even for `--since`, which becomes a derived ISO-8601 string but goes through a `?` binding). `ORDER BY` uses a column-name map (NOT direct user-string injection): `{"started": "started_at", "ended": "ended_at", "duration": "(julianday(ended_at) - julianday(started_at))", "events": "event_count", "tokens": "total_tokens", "cost": "total_tokens"}` — `cost` mapped to `total_tokens` in v0.1 since item #5 not yet landed; document this in the help text.
7. **Duration parser.** Tiny regex `^(\d+)([smhdw])$` (case-insensitive), table lookup `{"s":1, "m":60, "h":3600, "d":86400, "w":604800}`. Reject zero/negative. Returns `timedelta`. Wrapper for argparse `type=` raises `ArgumentTypeError`.
8. **Formatters.** `format_plain(rows)` — fixed widths from `max(len(...))` per column, single-pass; `format_rich(rows)` — `rich.table.Table` with `box=rich.box.SIMPLE`; `format_json(rows)` — `json.dumps([dict(row) for row in rows], indent=2)` plus all derived totals already in the row. JSON output uses snake_case (prompt requirement) — column names already snake_case from SQL, no transformation needed.
9. **Duration display.** Pure function `_format_duration(start_iso, end_iso) -> str`. Parse both, subtract, format as `<Nd><Nh><Nm><Ns>` with leading zeros dropped. Return `"-"` if `end_iso` is `None`.
10. **CLI wiring.** Remove `"ls"` from `_STUB_SUBCOMMANDS`. Add the new elif branch. Add `_run_ls`. Update `tests/test_cli_smoke.py`.
11. **Tests.** Write `tests/test_ls.py` after the implementation stabilises (TDD optional here — happy path is straightforward; the edge cases are where tests earn their keep, particularly `--reindex`, schema drift, malformed state, and idempotency).

### Architectural recommendations

- **Keep `ls.py` independent.** Do NOT import `capture` or `tail` at runtime. Duplicate the small helpers. This matches the pattern from `tail.py` and is justified there: failure contracts diverge. `ls`'s "fail-loud user CLI" contract is a third one (different from both `capture`'s fail-open and `tail`'s "fail-loud for batch CLI"), reinforcing the case for isolation.
- **Single `_data_root()` definition is fine as duplicated code.** The DRY violation across three modules is intentional — coupling the three modules to a single helper module would create an import dependency for a 5-line function. The prompt explicitly says "duplicate the ~10 lines."
- **Don't extract a shared `_io.py` yet.** This was deferred to v0.2+ per the `tail.py` module docstring. `ls.py` is the third caller of `_data_root` and friends; that's still not enough signal to extract. Wait until `cost` (item #5) and `view` (item #6) land, then revisit at v0.2 when there are 5 callers with diverging needs.
- **Schema-version table is per-table, not per-database.** The prompt is specific: "drop and rebuild table (NOT the whole DB file — preserve other tables a future feature might add)." Implement this by storing the version in a row tied to the table name OR by accepting a global `schema_version` table for now (simpler) and only dropping the `runs` table on mismatch. Recommend the simpler approach for v0.1; the prompt's SQL uses a single `schema_version` table so go with that.
- **`Path` vs `str` for SQLite.** `sqlite3.connect(...)` accepts `str | os.PathLike`. Use the `Path` object directly; `os.fspath()` is implicit. Avoid stringifying early.
- **Connection lifecycle.** Open once at the top of `run_ls`, pass `conn` to refresh and query, close in a `try/finally` (or `with contextlib.closing(conn):` — slightly cleaner). Don't use a module-level connection — that breaks the AGENTLOG_HOME monkeypatching pattern in tests.
- **No pragmas needed for v0.1.** Default journal mode (DELETE) is fine; default sync (FULL) is fine for a single-writer cache. WAL mode would help concurrent `ls` invocations but is optional optimization, not v0.1 scope.

### Test plan summary (for AC coverage)

| Acceptance criterion | Test |
|---|---|
| Empty runs dir → `"no runs found at ..."` + exit 0 | `test_empty_runs_dir_prints_and_exits_zero` |
| Populated dir sorted by started_at desc | `test_populated_dir_default_sort` (seed via 2-3 `tail.run_tail` invocations) |
| `--sort tokens --reverse` | `test_sort_tokens_reverse` |
| `--source sdk` / `--source hooks` / `--source all` | `test_source_filter` parametrised |
| `--since 1h` / `--since 7d` | `test_since_filter` w/ monkeypatched clock or backdated state |
| `--since garbage` → exit 2 | `test_since_invalid_exits_two` |
| `--json` machine-readable | `test_json_output_round_trips` (json.loads + assert keys present) |
| `--reindex` rebuilds | `test_reindex_rebuilds_table` (mutate row directly, --reindex, verify reset) |
| `--limit 3` caps output | `test_limit_caps_rows` |
| Steady-state speed (no full re-walk when nothing changed) | `test_idempotent_refresh_does_not_reread_json` (spy on `_index_run` via monkeypatch) |
| Index file at default path | `test_index_file_created_at_expected_path` |
| Index not written by `tail` / `_hook` | Implicit — no tests required; just don't import index code from those modules |
| 109 existing tests still pass + ruff + mypy strict clean | CI step; no specific new test |
| Malformed state.json | `test_malformed_state_json_skipped_with_warning` |
| `cost.json` missing | `test_missing_cost_json_yields_zero_tokens` |
| Schema version mismatch | `test_future_schema_version_drops_and_rebuilds` |
