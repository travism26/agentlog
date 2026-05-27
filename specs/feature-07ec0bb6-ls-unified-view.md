# Feature: `agentlog ls` — unified-view listing across hooks + SDK runs

## Metadata

adw_id: `07ec0bb6`
prompt: `/tmp/agentlog_step4_prompt.md`

## Feature Description

Implement v0.1 ship-scope item #4 from `DESIGN.md`: **`agentlog ls`** — the first read-time
tool in the project. It scans `~/.agentlog/runs/` (or `$AGENTLOG_HOME/runs/`) for every
captured run from BOTH data sources (interactive hooks-mode sessions written by
`capture.py`, and scripted SDK-mode runs ingested by `tail.py`) and prints a single
unified table sorted by start time. Filtering (`--source`, `--since`), sorting
(`--sort`, `--reverse`), pagination (`--limit`), and machine-readable output (`--json`)
are supported.

To keep `ls` fast even with thousands of runs, the module maintains a small SQLite
**cache** at `$AGENTLOG_HOME/index.sqlite3` populated from per-run `state.json` and
`cost.json` mtimes. The SQLite file is a *cache, never the source of truth* — the
per-run JSON files remain canonical and `ls` is **read-only with respect to `runs/`**.
On every invocation, `ls` stats each run dir's `state.json` + `cost.json` and only
re-reads the JSON for runs whose mtimes changed since they were last indexed.

`ls` is fail-loud (it's a user CLI, not a hook hot-path): user errors (e.g. bad
`--since`) exit 2 with a clear stderr message; unexpected I/O failures exit 1;
empty trees and successful renders exit 0.

This feature is the artifact that pays for the capture infrastructure landed in
items #1–3 (`init` / `_hook` / `tail`) — until `ls` exists, users have no daily
reason to open agentlog.

## User Story

As a developer using AI coding agents
I want to see every Claude Code session I've run (interactive AND scripted) in one
sortable list with start time, duration, event count, total tokens, and model
So that I can answer the questions "what did I run today?", "which run was longest?",
and "which run burned the most tokens?" without `cat`-ing JSONL files by hand.

## Problem Statement

Items #1–3 of v0.1 are landed (`agentlog init`, `_hook`, `tail`), so the user's
`~/.agentlog/runs/` directory is now filling up with per-run JSON. But today the only
way to see what's there is shelling `ls ~/.agentlog/runs/`, which returns nothing but
opaque session-id directory names — no timestamps, no cost, no source discrimination,
no sort. The capture infrastructure is therefore "write-only" from the user's
perspective: data flows in, but nothing flows out in a digestible form.

This blocks the three remaining v0.1 read-time commands (`cost`, `view`) from being
useful too, because users need a way to *discover* the run id to pass to them.

## Solution Statement

Add `src/agentlog/ls.py` exporting one public function:

```python
def run_ls(
    *,
    source: str,             # "all" | "hooks" | "sdk"
    since: timedelta | None, # filter: only runs started within DURATION
    sort_key: str,           # "started" | "ended" | "duration" | "events" | "tokens" | "cost"
    reverse: bool,           # ascending instead of default descending
    limit: int,              # 0 = unlimited
    as_json: bool,           # machine-readable JSON output
    reindex: bool,           # force a full SQLite rebuild before listing
) -> int
```

Internally, `ls`:

1. Opens (or creates) `$AGENTLOG_HOME/index.sqlite3` with a single `runs` table and a
   `schema_version` table (version=1). On schema-version mismatch, drops the `runs`
   table and `schema_version` row and rebuilds (preserves other tables a future
   feature might add).
2. Walks `$AGENTLOG_HOME/runs/`. For each subdirectory containing `state.json`,
   compares the current mtime of `state.json` + `cost.json` against what's stored in
   the index. Only re-reads JSON for stale rows. Purges rows for run dirs that no
   longer exist. `--reindex` truncates and re-walks unconditionally.
3. Builds a parameterised `SELECT` with `WHERE` clauses for `source` and `since`,
   `ORDER BY` mapped from a fixed column dictionary (NEVER raw user input), and
   `LIMIT` from the validated `--limit` argument.
4. Formats output: plain whitespace-aligned ASCII columns by default (stdlib only,
   pipe-friendly), `rich.table.Table` when `rich` is installed AND `sys.stdout.isatty()`,
   or a JSON array when `--json` is set.

CLI surface added to `src/agentlog/cli.py`:

```
agentlog ls [--source hooks|sdk|all] [--since DURATION] [--sort COLUMN]
            [--reverse] [--limit N] [--json] [--reindex]
```

`pyproject.toml dependencies = []` stays empty — `sqlite3` is stdlib. `rich` remains
gated by the existing `[tui]` extra and a `try/except ImportError`. Capture and tail
modules are NOT touched.

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` — Locked v0.1 design (lines 87-131 data-flow; line 193 ship-scope row
  for `ls`); the source of truth for scope and architecture.
- `CLAUDE.md` — Hard rules: #6 local-first / no network; #7 schema versioning + fail
  tolerant; "Stack" section locks stdlib-only runtime + `rich` as optional extra.
- `ai_docs/research/07ec0bb6-ls-unified-view-analysis.md` — Pre-planning research
  doc; covers existing patterns, helper-duplication convention, schema fields
  written by `capture.py` and `tail.py`, and the full test plan.
- `src/agentlog/cli.py` — Subparser wiring lives here. Remove `"ls"` from
  `_STUB_SUBCOMMANDS` (line 14), add a new `elif name == "ls":` branch in the
  build-parser loop (insert after the `tail` branch at line 87), and add a
  `_run_ls(args)` shim below `_run_tail` (line ~109). Add `ls` to the
  `from agentlog import ...` line at line 10.
- `src/agentlog/_constants.py` — Add `INDEX_FILE_NAME = "index.sqlite3"` and
  `INDEX_SCHEMA_VERSION = 1`. Keep distinct from the existing `SCHEMA_VERSION` (which
  refers to the JSONL wire schema).
- `src/agentlog/capture.py` — Read-only reference: documents what hooks-mode
  `state.json` (lines 130-156) and `cost.json` (lines 264-281) contain. Source of
  the `_data_root` / `_log_self` / `_isoformat` helpers to duplicate into `ls.py`.
- `src/agentlog/tail.py` — Read-only reference: documents SDK-mode `state.json`
  (lines 345-360) and `cost.json` (lines 443-452); confirms the parser is uniform
  across sources. Pattern reference for module structure, helper duplication, and
  `_run_<command>` → `run_<command>` shim shape.
- `pyproject.toml` — `dependencies = []` stays empty (`sqlite3` is stdlib).
  `[tui]` extra already declares `rich>=13.7`. No edits needed. ruff line-length=100
  and mypy strict on `src` + `tests` apply.
- `tests/test_cli_smoke.py:25` — Extend the "not yet implemented" exclusion set to
  add `"ls"`.
- `tests/test_tail.py` — Pattern reference for `tests/test_ls.py` (monkeypatched
  `AGENTLOG_HOME`, fixture-driven seeding via `tail.run_tail`).
- `tests/fixtures/sdk_minimal.jsonl` — Existing SDK fixture; new `ls` tests seed
  run dirs by calling `tail.run_tail` against it rather than hand-writing JSON.

### New Files

- `src/agentlog/ls.py` — New module. Public surface: `run_ls(...) -> int`.
  Internals: SQLite open / init / schema-migrate, refresh-on-stale walker,
  `_index_run`, query builder with parameterised filters + safe `ORDER BY` mapping,
  table formatter (plain), `rich` formatter (gated on import success + TTY), JSON
  formatter, `--since` duration parser. Duplicates `_data_root`, `_log_self`,
  `_isoformat` from `capture.py` per the established "fail-loud CLIs duplicate
  helpers" convention (do NOT runtime-import `capture` or `tail`).
- `tests/test_ls.py` — New test file. Coverage per acceptance criteria, fixtures
  seeded by `tail.run_tail` and direct `capture.dispatch` calls, monkeypatched
  `AGENTLOG_HOME`.

## Implementation Plan

### Phase 1: Foundation

Add the two new constants (`INDEX_FILE_NAME`, `INDEX_SCHEMA_VERSION`) and the new
`ls.py` module skeleton with module docstring, duplicated helpers, and an empty
`run_ls` returning 0. Pull `"ls"` out of `_STUB_SUBCOMMANDS` and update
`tests/test_cli_smoke.py` to reflect that `ls` is no longer a stub. Compile-checks
clean at this point, but `ls` is still a no-op.

### Phase 2: Core Implementation

Implement the SQLite layer (open, init schema, version check + drop-on-mismatch),
the refresh-on-stale walker (`_refresh_index`), the per-run indexer (`_index_run`),
the duration parser for `--since`, the query builder, and the three output
formatters (plain / rich / JSON). Each piece behind a private helper, all types
strict, all SQL parameterised.

### Phase 3: Integration

Wire the `ls` subparser into `cli.py` with all flags and the `_run_ls` shim. Write
`tests/test_ls.py` covering the full acceptance-criteria matrix (happy path,
filters, sort variants, JSON output, `--reindex`, idempotency, malformed inputs,
schema-version drift). Run ruff + mypy strict + pytest; fix any drift.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Add index constants

- Edit `src/agentlog/_constants.py`.
- Add `INDEX_FILE_NAME: str = "index.sqlite3"` after `RUNS_DIR_NAME`.
- Add `INDEX_SCHEMA_VERSION: int = 1` after `INDEX_FILE_NAME`.
- Update the module docstring to add a third pinned note: "`INDEX_SCHEMA_VERSION`
  is the version of the SQLite cache schema used by `ls`. It is independent of
  `SCHEMA_VERSION` (the JSONL wire-format version). Bumping `INDEX_SCHEMA_VERSION`
  triggers a drop-and-rebuild of the `runs` table — see `ls.py`."

### 2. Create `src/agentlog/ls.py` skeleton

- Create the file.
- `from __future__ import annotations` at the top.
- Module docstring with four pinned invariants:
  1. "The SQLite index at `$AGENTLOG_HOME/index.sqlite3` is a *cache*, never the
     source of truth — `runs/<id>/{state,events,cost}.json` remain canonical."
  2. "`ls` is read-only with respect to `runs/`. The only file it ever writes is
     the index."
  3. "Failure contract is **fail-loud**: this is a user CLI, not a hook hot-path.
     User errors exit 2; runtime I/O failures exit 1; success and empty trees
     exit 0."
  4. "Schema versioning: on mismatch, drop the `runs` table and the
     `schema_version` row, then re-create. Preserves other tables a future
     feature might add."
- Duplicate `_data_root()`, `_log_self()`, `_isoformat()` from `capture.py`
  verbatim (mirror the convention `tail.py` already uses).
- Stub `def run_ls(...) -> int: return 0` matching the public-surface signature
  declared in the Solution Statement.

### 3. Implement SQLite open + schema init + version check

- `_open_index(root: Path) -> sqlite3.Connection`. Sets
  `connection.row_factory = sqlite3.Row`. Calls `_init_schema(conn)` before
  returning.
- `_init_schema(conn)`. Executes the `CREATE TABLE IF NOT EXISTS runs (...)`
  + indexes + `schema_version` block exactly as specified in the prompt.
- `_check_schema_version(conn)`. Reads the current version row.
  If absent OR mismatched (`!= INDEX_SCHEMA_VERSION`): `DROP TABLE runs`,
  `DELETE FROM schema_version`, then re-call `_init_schema(conn)`.

### 4. Implement the refresh-on-stale walker

- `_refresh_index(conn, runs_root: Path) -> None` matching the algorithm in the
  prompt.
- Skip non-directory entries.
- Skip dirs without `state.json` (incomplete / mid-write).
- Compare stored `state_mtime` + `cost_mtime` to current file mtimes; only call
  `_index_run` when stale (or no row exists).
- After the walk, delete rows whose `run_id` no longer corresponds to any seen
  directory.
- `conn.commit()` once at the end.

### 5. Implement `_index_run`

- `_index_run(conn, run_dir, state_path, cost_path, state_mtime, cost_mtime)`.
- Local `_read_json(path)` helper: best-effort `json.loads`; on `JSONDecodeError`
  or `OSError` returns `None` and logs via `_log_self`; caller prints a stderr
  warning (`"warning: skipped <id> (malformed state.json)"`) and `return`s
  without writing a row.
- Extract fields tolerantly: `state.get("source")`, `state.get("session_id")`,
  `state.get("parent_session_id")`, `state.get("started_at")`,
  `state.get("ended_at")`, `state.get("cwd")`, `state.get("model")`,
  `int(state.get("event_count", 0))`.
- For tokens: read `cost.get("totals", {})` and sum the four kinds
  (`input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens`); default each to 0 if absent.
- `INSERT OR REPLACE INTO runs (...) VALUES (...)` parameterised.
- `indexed_at` set via `_isoformat(datetime.now(UTC))`.

### 6. Implement `--since` duration parser

- `_parse_duration(text: str) -> timedelta`. Regex `^(\d+)([smhdw])$`,
  case-insensitive. Map `s/m/h/d/w` → 1 / 60 / 3600 / 86400 / 604800 seconds.
- Reject zero or negative magnitudes.
- Raise `argparse.ArgumentTypeError` with a clear message on parse failure
  (argparse formats this correctly and exits 2 automatically).

### 7. Implement the query layer

- `_query_runs(conn, *, source, since, sort_key, reverse, limit) -> list[sqlite3.Row]`.
- Build `WHERE` clauses incrementally:
  - if `source != "all"`: append `source = ?` and append to params.
  - if `since is not None`: derive `cutoff = datetime.now(UTC) - since`,
    `_isoformat(cutoff)`, append `started_at >= ?` and append to params.
- Build `ORDER BY` from a fixed lookup dict (NEVER user-string-injected):

  ```python
  SORT_COLUMN_MAP = {
      "started":  "started_at",
      "ended":    "ended_at",
      "duration": "(julianday(ended_at) - julianday(started_at))",
      "events":   "event_count",
      "tokens":   "total_tokens",
      "cost":     "total_tokens",   # v0.1: alias of tokens until item #5 lands
  }
  ```

- Direction: `ASC` when `reverse=True`, `DESC` otherwise (default sort is
  newest-first because "what did I just run?" is the common question).
- Append `LIMIT ?` only if `limit > 0`; `limit=0` means unlimited.

### 8. Implement the duration display formatter

- `_format_duration(start_iso: str | None, end_iso: str | None) -> str`.
- Returns `"-"` when either is `None`.
- Parses with `datetime.fromisoformat`; subtracts; formats as
  `<Nd><Nh><Nm><Ns>` with leading zero-units dropped (`"8m41s"`, `"3h14m"`,
  `"3d2h"`). Never microseconds.

### 9. Implement the plain-text formatter

- `_format_plain(rows) -> str`.
- Columns: `RUN ID`, `SOURCE`, `STARTED`, `DUR`, `EVENTS`, `TOKENS`, `MODEL`.
- Compute column widths via `max(len(str(...)))` per column (plus header).
- `STARTED` formatted as ISO-8601 UTC, second precision, `Z` suffix (NEVER
  local time; pasted into bug reports → timezones cause confusion).
- `TOKENS` rendered with comma thousands; show `0` (not `-`) for runs with no
  cost data.
- `MODEL` shows `-` when null.
- Do NOT truncate `RUN ID` — the user copies it into `view <id>`.
- No ANSI colors.

### 10. Implement the rich formatter (optional)

- `_format_rich(rows) -> str | None`.
- Lazy `try: from rich.table import Table; from rich.console import Console
  except ImportError: return None` at the top of the function (NOT at module
  level — keep `ls.py` importable in the stdlib-only base install).
- Gated additionally on `sys.stdout.isatty()` (caller-side); when piped or in
  CI, fall back to `_format_plain`.
- Same columns + rendering rules as plain; `box=rich.box.SIMPLE`; render to a
  `Console(file=StringIO())` and return the captured string (keeps `run_ls`
  single-print).

### 11. Implement the JSON formatter

- `_format_json(rows) -> str`.
- `json.dumps([dict(row) for row in rows], indent=2)` plus the derived
  `duration` string per row.
- All keys snake_case (already are, since they come from column names).
- Stable schema (document the field list in the function docstring).

### 12. Implement `run_ls` orchestration

- `run_ls(*, source, since, sort_key, reverse, limit, as_json, reindex) -> int`.
- Resolve `root = _data_root()`.
- `runs_root = root / RUNS_DIR_NAME`.
- If `runs_root` does not exist: print `f"no runs found at {runs_root}"` to
  stdout; return 0. **Do NOT create the index file in this case** — preserves
  the "writes nothing if there's nothing to do" invariant.
- Open the index via `contextlib.closing(_open_index(root / INDEX_FILE_NAME))`.
- If `reindex`: `DROP TABLE runs`, `_init_schema(conn)` to recreate (then fall
  through to `_refresh_index` to repopulate).
- `_refresh_index(conn, runs_root)`.
- `rows = _query_runs(conn, ...)`.
- If `as_json`: print `_format_json(rows)`; return 0.
- Else: try `_format_rich(rows)` (gated on TTY + import); fall back to
  `_format_plain(rows)`; print; return 0.
- Catch `sqlite3.DatabaseError` / `OSError` at the top of `run_ls`; log via
  `_log_self`; print clear error to stderr; return 1.

### 13. Wire the CLI subparser

- Edit `src/agentlog/cli.py`.
- Update `from agentlog import ...` to add `ls`.
- Remove `"ls"` from `_STUB_SUBCOMMANDS` on line 14.
- Add a new `elif name == "ls":` branch in the build-parser loop, after the
  `tail` branch ending at line 87:
  - `sp = sub.add_parser("ls", help="list captured runs across hooks and SDK
    sources")`
  - `sp.add_argument("--source", choices=["hooks", "sdk", "all"], default="all", ...)`
  - `sp.add_argument("--since", type=ls._parse_duration, default=None, ...)`
  - `sp.add_argument("--sort", dest="sort_key",
    choices=["started", "ended", "duration", "events", "tokens", "cost"],
    default="started", ...)`
  - `sp.add_argument("--reverse", action="store_true", ...)`
  - `sp.add_argument("--limit", type=int, default=50, ...)`
  - `sp.add_argument("--json", dest="as_json", action="store_true", ...)`
  - `sp.add_argument("--reindex", action="store_true", ...)`
  - `sp.set_defaults(func=_run_ls)`
- Add `_run_ls(args)` helper below `_run_tail`:

  ```python
  def _run_ls(args: argparse.Namespace) -> int:
      return ls.run_ls(
          source=args.source,
          since=args.since,
          sort_key=args.sort_key,
          reverse=args.reverse,
          limit=args.limit,
          as_json=args.as_json,
          reindex=args.reindex,
      )
  ```

### 14. Update the smoke test exclusion set

- Edit `tests/test_cli_smoke.py:25`.
- Change the exclusion set from `{"init", "uninstall", "tail"}` to
  `{"init", "uninstall", "tail", "ls"}`.

### 15. Create `tests/test_ls.py`

- Mirror `tests/test_tail.py` structure.
- `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` fixture.
- Fixture builders:
  - `seed_sdk_run(...)`: call `tail.run_tail(...)` against
    `tests/fixtures/sdk_minimal.jsonl` (with `--force --run-id sdk-test-N`).
  - `seed_hooks_run(...)`: call `capture.dispatch(...)` (or
    `capture.run_hook` with crafted env) to produce a synthetic hooks-mode
    run dir.
- Test cases (one function each):
  - `test_empty_runs_dir_prints_and_exits_zero`
  - `test_populated_dir_default_sort_started_desc`
  - `test_sort_tokens_reverse_puts_lowest_first`
  - `test_source_filter_sdk_only`
  - `test_source_filter_hooks_only`
  - `test_source_filter_all_default`
  - `test_since_filter_1h_only_recent`
  - `test_since_filter_7d`
  - `test_since_invalid_exits_two`
  - `test_json_output_round_trips`
  - `test_reindex_rebuilds_table` (mutate a row directly via sqlite3,
    invoke `--reindex`, assert the row is back in sync with the JSON)
  - `test_limit_caps_rows`
  - `test_idempotent_refresh_does_not_reindex_unchanged_runs` (monkeypatch
    `_index_run`, run `ls` twice, assert the spy was called only on the
    first invocation)
  - `test_index_file_created_at_expected_path`
  - `test_malformed_state_json_skipped_with_warning` (capture stderr, assert
    warning, assert other rows still listed)
  - `test_missing_cost_json_yields_zero_tokens`
  - `test_future_schema_version_drops_and_rebuilds_runs_table` (manually
    insert schema_version=999, run `ls`, assert table got dropped + rebuilt
    cleanly)
  - `test_missing_runs_dir_does_not_create_index_file`
  - `test_duration_format_human_readable` (unit test of `_format_duration`)
  - `test_parse_duration_accepts_valid_suffixes` (unit test of
    `_parse_duration`)

### 16. Compile + lint + type + test checks

- Run `.venv/bin/python -m py_compile src/agentlog/ls.py src/agentlog/cli.py
  src/agentlog/_constants.py`.
- Run `.venv/bin/python -c "from agentlog import ls, cli; print('import OK')"`.
- Run `.venv/bin/agentlog ls --help` (verify help text renders).
- Run `.venv/bin/ruff check src tests`.
- Run `.venv/bin/mypy src tests` (strict mode per `pyproject.toml`).
- Run `.venv/bin/pytest -q` and ensure all 109 existing tests plus the new
  `test_ls.py` cases pass.

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation. The
repository has no `HOW_TO_CREATE_TESTS.md` / `TESTING.md` — follow the patterns
in existing `tests/test_capture.py` and `tests/test_tail.py`. Both files
monkeypatch `AGENTLOG_HOME` to a `tmp_path` fixture and seed runs by calling
production functions (not by hand-writing JSON), which is the established
convention to keep tests robust against schema evolution.

### Unit Tests

- `_parse_duration` — valid suffixes (`s`, `m`, `h`, `d`, `w`), case-insensitive
  parsing, rejection of zero / negative / non-integer / garbage.
- `_format_duration` — null end-time → `"-"`, second-precision values, multi-day
  values (`3d2h`), single-unit values (`8m41s`), microseconds NEVER appearing.
- `SORT_COLUMN_MAP` keys match `--sort` argparse choices (defensive: tests guard
  against future drift between the argparse declaration and the SQL column map).
- `_format_plain` — column-width calculation correctness with mixed-length run
  ids; `0` shown for token totals not `-`; `RUN ID` never truncated; `STARTED`
  always UTC `Z`-suffixed.
- `_format_json` — round-trippable through `json.loads`; key names snake_case;
  derived `duration` field present.

### Integration Tests

- End-to-end via `subprocess.run([sys.executable, "-m", "agentlog", "ls", ...])`
  for at least one happy-path case (smoke test that argparse wiring + module
  routing + output rendering all line up).
- `tail.run_tail` against `tests/fixtures/sdk_minimal.jsonl` + `ls` shows the
  ingested run with `source="sdk"`, populated tokens, populated model.
- `capture.dispatch(...)` (or equivalent SessionStart + Stop sequence) + `ls`
  shows the hooks-mode run with `source="hooks"`, `model="-"` until the agent
  populates state, sensible event count.
- Two-source mixed test: seed 2 SDK runs + 2 hooks runs, assert default `ls`
  shows all 4 sorted by `started_at DESC`, `--source sdk` shows 2, `--source
  hooks` shows 2.

### Edge Cases

- `~/.agentlog/runs/` doesn't exist → `"no runs found at ..."` to stdout, exit 0,
  index file NOT created.
- A `runs/<id>/` directory missing `state.json` (simulated mid-write / crashed
  run) → silently skipped, no crash, no warning.
- A `state.json` parsing successfully but missing fields (`event_count`,
  `model`) → defaults applied (`0` / `None`), no crash.
- A `state.json` failing JSON decode → logged to `_self.log`, stderr warning
  printed, row skipped, exit 0 with other rows still listed.
- `cost.json` missing → `total_tokens=0` in the row, `cost_mtime=0.0` sentinel
  stored.
- `--since 24h` against an all-older tree → empty table, exit 0.
- `--since garbage` → exit 2 with clear stderr error (argparse formats it).
- `--sort invalid` → argparse rejects pre-DB-touch with exit 2.
- `--source` is a single-value choice (not a flag set) → mutually exclusive by
  construction.
- Two parallel `ls` invocations → both complete; one may do redundant work;
  documented as a known limitation, not a failure.
- SQLite file with `schema_version=999` (future-schema scenario, or another
  tool wrote it) → `runs` table dropped and rebuilt; other tables in the same
  DB file preserved.
- Run IDs are UUIDs / `sdk-` prefixed UUIDs → ASCII-safe; defensive `str(...)`
  cast in formatters.

## Acceptance Criteria

- `agentlog ls` against an empty `~/.agentlog/runs/` prints
  `"no runs found at <path>"` and exits 0.
- `agentlog ls` against a populated dir (seeded via `agentlog tail
  agents/1b4319ab/` or test fixture) prints all ingested runs sorted by
  `started_at` desc, with the columns `RUN ID / SOURCE / STARTED / DUR /
  EVENTS / TOKENS / MODEL` all populated.
- `agentlog ls --sort tokens --reverse` puts the lowest-token run on top.
- `agentlog ls --source sdk` shows only SDK runs; `--source hooks` only hooks
  runs; default `--source all` shows both.
- `agentlog ls --since 1h` filters to recent runs only; `--since 7d` to the
  last week.
- `agentlog ls --since garbage` exits 2 with a clear stderr error.
- `agentlog ls --json` outputs a JSON array parseable by `jq`;
  `jq '.[0].run_id'` returns the most-recent run id.
- `agentlog ls --reindex` rebuilds the SQLite table and produces the same
  output as a regular `ls`.
- `agentlog ls --limit 3` returns at most 3 rows.
- Re-running `agentlog ls` is fast: no full re-walk of `events.jsonl`, only one
  `stat()` per run and one `SELECT` per stale run.
- The SQLite index file is created on first invocation at
  `$AGENTLOG_HOME/index.sqlite3` (default `~/.agentlog/index.sqlite3`), but NOT
  when `runs/` is absent.
- The index file is NOT mutated by `tail` or `_hook` — only by `ls`.
- All 109 existing tests still pass; new `tests/test_ls.py` covers every
  acceptance-criterion case above plus the edge cases listed.
- `ruff check src tests` clean.
- `mypy src tests` (strict) clean.
- No new runtime dependencies in `pyproject.toml`
  (`dependencies = []` stays empty).
- `README.md` is NOT touched; docs are deferred to the docs phase (output goes
  to `docs/feature-07ec0bb6-ls-unified-view.md`, generated separately).

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These
run during the build phase — do NOT include pytest, linters, or pipeline runs
(those belong to dedicated CI phases).

- `.venv/bin/python -m py_compile src/agentlog/ls.py src/agentlog/cli.py src/agentlog/_constants.py && echo "OK"` — Verify no syntax errors.
- `.venv/bin/python -c "from agentlog import ls, cli, _constants; print('import OK')"` — Verify modules import cleanly with no side effects.
- `.venv/bin/python -c "from agentlog.ls import run_ls, _parse_duration, _format_duration; print('symbols OK')"` — Verify the public + key private symbols exist.
- `.venv/bin/agentlog --help` — Verify CLI still works and `ls` no longer says "not yet implemented".
- `.venv/bin/agentlog ls --help` — Verify the new subparser's help text renders all flags.

## Notes

- **No new libraries.** `sqlite3` is stdlib; `rich` already declared under the
  `[tui]` optional-extra. `uv add` is NOT needed.
- **Privacy.** `ls` performs **zero** network calls. The SQLite index is local;
  contents are derived from local JSON. Local-first principle (CLAUDE.md
  hard rule #6) preserved.
- **Helper duplication is intentional** — `_data_root`, `_log_self`,
  `_isoformat` are duplicated into `ls.py` rather than imported from `capture`
  or `tail`. Reason: failure contracts diverge across the three modules
  (`capture` is fail-open in the hook hot-path; `tail` is fail-loud for batch
  CLI; `ls` is fail-loud for an interactive user CLI). Coupling them would
  create an import dependency for a 5-line function across three distinct
  failure surfaces. Refactor deferred until 5+ callers exist with diverging
  needs (probably v0.2+ once `cost` and `view` land).
- **`cost` sort alias.** `--sort cost` is mapped to `total_tokens` in v0.1 (and
  the `--sort` help text says so). When v0.1 ship-scope item #5 (`agentlog
  cost <id>`) lands and dollar-cost columns become available, `--sort cost`
  will be remapped to a true cost column. The argparse choice keeps the
  forward-compatible name now.
- **Concurrency note.** Two parallel `ls` invocations may both attempt to
  refresh the same stale row. SQLite handles last-writer-wins. Documented as a
  known limitation (worst case: redundant work; correctness preserved). WAL
  mode is a future optimization, not v0.1 scope.
- **Schema-version drift.** The prompt prescribes "drop the `runs` table and
  the `schema_version` row, then re-create" (NOT delete the whole DB file).
  This forward-compatible posture preserves other tables that future features
  (e.g. v0.2+ per-event indexing) may add.
- **No `PreToolUse` / no v0.2+ features.** This task strictly implements
  ship-scope item #4. `agentlog cost <id>` (item #5), `agentlog view <id>`
  (item #6), `agentlog diff` (v0.2+), full-text event search (v0.2+), web
  dashboard (never), and per-event indexing in SQLite (v0.2+) are all out of
  scope.
- **Future refactor candidate.** Once items #5 and #6 land, three modules will
  duplicate `_data_root` / `_log_self` / `_isoformat`. At that point (5
  callers), extracting `src/agentlog/_io.py` becomes justified — but only with
  a shared failure contract that all callers can accept. Track this as a v0.2
  cleanup, not a blocker for shipping `ls`.
