# `agentlog ls` — Unified Run Listing

**ADW ID:** 07ec0bb6
**Date:** 2026-05-27
**Specification:** specs/feature-07ec0bb6-ls-unified-view.md

## Overview

Implements `agentlog ls`, the first read-time command in agentlog. It scans `~/.agentlog/runs/` for every captured run from both data sources (interactive hooks-mode sessions and scripted SDK runs) and prints a unified, sortable table. A SQLite cache (`index.sqlite3`) keeps repeated invocations fast via mtime-based staleness detection — the per-run JSON files remain the canonical source of truth.

## What Was Built

- `src/agentlog/ls.py` — new module implementing the full `ls` pipeline
- SQLite index at `$AGENTLOG_HOME/index.sqlite3` with schema-version migration
- Refresh-on-stale walker that re-indexes only runs whose `state.json` or `cost.json` mtime changed
- `--source`, `--since`, `--sort`, `--reverse`, `--limit`, `--json`, `--reindex` CLI flags
- Plain-text formatter (stdlib, pipe-friendly), `rich` formatter (TTY-gated, optional), and JSON formatter
- `INDEX_FILE_NAME` and `INDEX_SCHEMA_VERSION` constants added to `_constants.py`
- `ls` subparser wired into `cli.py`; `tail` subparser and `_hook` routing also landed in the same CLI pass
- `tests/test_ls.py` — 20+ test cases covering happy path, filters, sort variants, edge cases, and schema migration

## Technical Implementation

### Files Modified

- `src/agentlog/ls.py`: new module; public surface `run_ls(...) -> int`
- `src/agentlog/_constants.py`: added `INDEX_FILE_NAME = "index.sqlite3"` and `INDEX_SCHEMA_VERSION = 1`; updated module docstring with third pinned note
- `src/agentlog/cli.py`: removed `"ls"` and `"tail"` from `_STUB_SUBCOMMANDS`; added `ls`, `tail`, and `_hook` subparsers; added `_run_ls`, `_run_tail`, `_run_hook` shims
- `tests/test_cli_smoke.py`: removed `"ls"` from the stub exclusion set
- `tests/test_ls.py`: new file

### Key Changes

- **Refresh-on-stale walker** (`_refresh_index`): stats each `state.json` + `cost.json` on every `ls` invocation; only calls `_index_run` when either mtime changed since last index. After the walk, purges SQLite rows for run dirs that no longer exist.
- **Schema-version migration** (`_check_schema_version`): on `INDEX_SCHEMA_VERSION` mismatch, drops only the `runs` table and `schema_version` row, then re-creates — preserves any future tables in the same DB file.
- **Parameterised query builder** (`_query_runs`): `ORDER BY` is resolved through a fixed `SORT_COLUMN_MAP` dict; user input never interpolated into SQL strings.
- **`--since` duration parser** (`_parse_duration`): regex `^(\d+)([smhdw])$`; raises `argparse.ArgumentTypeError` on invalid input so argparse formats the error and exits 2 automatically.
- **Helper duplication**: `_data_root`, `_log_self`, `_isoformat` are copied from `capture.py` rather than imported — failure contracts differ (`ls` is fail-loud; `capture` is fail-open in the hook hot-path).

### Architecture Impact

`ls` is purely a read-time command. It never writes to `runs/` and does not touch the hook hot-path. The index file (`index.sqlite3`) is the only file `ls` creates or modifies. `tail` and `capture` do not know the index exists — cache invalidation is lazy (mtime-based) rather than event-driven. This preserves hook latency budgets and the fail-open contract.

## How to Use

### CLI Commands

```bash
# List all captured runs (default: sorted by start time, newest first, limit 50)
agentlog ls

# Filter by data source
agentlog ls --source hooks
agentlog ls --source sdk
agentlog ls --source all   # default

# Filter to recent runs
agentlog ls --since 1h
agentlog ls --since 24h
agentlog ls --since 7d

# Sort and order
agentlog ls --sort tokens            # sort by total tokens (descending)
agentlog ls --sort duration --reverse  # shortest run first

# Pagination
agentlog ls --limit 10
agentlog ls --limit 0   # unlimited

# Machine-readable output
agentlog ls --json
agentlog ls --json | jq '.[0].run_id'

# Force full index rebuild
agentlog ls --reindex

# All flags combined
agentlog ls --source sdk --since 7d --sort tokens --limit 20 --json
```

### Programmatic Usage

```python
from agentlog.ls import run_ls
from datetime import timedelta

exit_code = run_ls(
    source="all",
    since=timedelta(hours=24),
    sort_key="started",
    reverse=False,
    limit=50,
    as_json=False,
    reindex=False,
)
```

## Configuration

| Environment variable | Default              | Effect                                      |
|----------------------|----------------------|---------------------------------------------|
| `AGENTLOG_HOME`      | `~/.agentlog`        | Root data directory; index and runs live here |

The SQLite index is created at `$AGENTLOG_HOME/index.sqlite3` on the first `ls` invocation against a populated `runs/` directory. If `runs/` does not exist, the index file is NOT created.

## Testing

```bash
pytest tests/test_ls.py -v
```

Key test cases:

| Test | What it verifies |
|------|-----------------|
| `test_empty_runs_dir_prints_and_exits_zero` | Empty `runs/` → message to stdout, exit 0, no index file |
| `test_populated_dir_default_sort_started_desc` | Populated dir → all rows present, newest first |
| `test_source_filter_sdk_only` / `_hooks_only` | `--source` filter narrows to correct subset |
| `test_since_filter_1h_only_recent` | `--since` cutoff excludes old runs |
| `test_since_invalid_exits_two` | Bad `--since` → exit 2 via argparse |
| `test_json_output_round_trips` | `--json` output parses cleanly; `run_id` field present |
| `test_reindex_rebuilds_table` | `--reindex` drops and repopulates; result matches non-reindex run |
| `test_idempotent_refresh_does_not_reindex_unchanged_runs` | `_index_run` not called on second identical `ls` |
| `test_malformed_state_json_skipped_with_warning` | Bad JSON → stderr warning, other rows still listed, exit 0 |
| `test_future_schema_version_drops_and_rebuilds_runs_table` | `schema_version=999` → drop + rebuild, clean exit |
| `test_missing_runs_dir_does_not_create_index_file` | No `runs/` → index file not created |

## Notes

- **No new dependencies.** `sqlite3` is stdlib; `rich` is already declared under the `[tui]` optional extra. `pyproject.toml` `dependencies = []` unchanged.
- **Local-first.** Zero network calls. All data stays in `$AGENTLOG_HOME`. Compliant with CLAUDE.md hard rule #6.
- **`--sort cost` alias.** In v0.1, `--sort cost` maps to `total_tokens` (documented in help text). When item #5 (`agentlog cost <id>`) lands and dollar-cost columns are available, the alias will be remapped. The argparse choice preserves the forward-compatible name.
- **Concurrency.** Two parallel `ls` invocations may both refresh the same stale row; SQLite last-writer-wins. Correctness is preserved; worst case is redundant work. WAL mode deferred to v0.2+.
- **Future refactor.** Once items #5 and #6 (`cost`, `view`) land, three modules will duplicate `_data_root` / `_log_self` / `_isoformat`. Extracting `src/agentlog/_io.py` becomes justified at that point (v0.2 cleanup).
- **`cost` sort column.** Dollar-cost data requires item #5. Until then `--sort cost` is an alias for `--sort tokens`.
