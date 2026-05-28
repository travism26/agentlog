# CLI Reference

Per-flag, per-exit-code reference for every agentlog subcommand. For the elevator pitch and install instructions, see [README.md](../README.md). For schema depth and architecture decisions, see [architecture.md](architecture.md).

All subcommands share a common exit-code convention: **0** = success (including "nothing to do"), **1** = unexpected I/O or runtime failure, **2** = user error (bad flags, mutually exclusive options, missing required argument). Exceptions are noted per-subcommand.

The data root defaults to `~/.agentlog`. Override with `AGENTLOG_HOME=<path>`.

---

## agentlog init

Register agentlog hook handlers in Claude Code's `settings.json`.

### Synopsis

```
agentlog init [--project] [--dry-run]
```

### Description

Reads the target `settings.json`, merges five hook entries (one per event in `EVENTS`) into the existing hooks configuration, and writes the result back atomically. The operation is **idempotent**: running it a second time prints "already installed" and exits 0 without rewriting the file.

The target file is `~/.claude/settings.json` (user-global scope) unless `--project` is given, in which case it is `./.claude/settings.json` in the current directory.

Each installed entry uses the `HOOK_COMMAND_PREFIX = "agentlog _hook"` discriminator so `uninstall` can identify and remove exactly the agentlog-owned entries without touching anything else. The file is written with `sort_keys=True` (stable diffs across machines; see [ADW lesson #6](adw-lessons.md)).

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--project` | flag | off | Write to `./.claude/settings.json` instead of `~/.claude/settings.json` |
| `--dry-run` | flag | off | Print the diff that would be applied; write nothing |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Hooks installed (or already present — no-op) |
| 1 | `settings.json` exists but is malformed JSON, or the file cannot be read/written |

### Examples

```bash
# Install hooks globally (default)
agentlog init

# Preview what would change without writing
agentlog init --dry-run

# Install into the current project's settings only
agentlog init --project

# Preview project-scoped install
agentlog init --project --dry-run
```

---

## agentlog uninstall

Remove agentlog hook entries from Claude Code's `settings.json`.

### Synopsis

```
agentlog uninstall [--project] [--dry-run]
```

### Description

The inverse of `init`. Reads the target `settings.json`, removes every entry whose command string starts with `HOOK_COMMAND_PREFIX = "agentlog _hook"`, and writes the result back atomically.

User-added hooks with different prefixes are **preserved**. If no agentlog entries are found, prints "nothing to uninstall" and exits 0. If the target file does not exist, exits 0 silently.

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--project` | flag | off | Write to `./.claude/settings.json` instead of `~/.claude/settings.json` |
| `--dry-run` | flag | off | Print the diff that would be applied; write nothing |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Hooks removed (or not present — no-op), or settings file absent |
| 1 | `settings.json` is malformed JSON, or the file cannot be read/written |

### Examples

```bash
# Remove hooks globally
agentlog uninstall

# Preview removal without writing
agentlog uninstall --dry-run

# Remove hooks from the current project's settings
agentlog uninstall --project
```

---

## agentlog tail

Ingest one or more `cc_raw_output.jsonl` files from Claude Code SDK runs into the unified `runs/<id>/` schema.

### Synopsis

```
agentlog tail <path> [--run-id RUN_ID] [--source-name SOURCE_NAME] [--dry-run] [--force]
```

### Description

`<path>` may be a single `cc_raw_output.jsonl` file or a directory. When given a directory, `tail` recurses up to 5 levels deep and processes every `cc_raw_output.jsonl` found.

The run ID is derived automatically from the `system/init` record's `session_id` field. Explicit `--run-id` is supported only for single-file ingestion; passing it with a directory containing multiple files exits 2.

Already-ingested runs are skipped (idempotent by default). Use `--force` to re-ingest and overwrite existing data.

Timestamp derivation: the file's mtime is used as the session END timestamp. The START timestamp is back-derived as `END − result.duration_ms` (the authoritative session length from the `result` record). If no `result` record is present (truncated session), the fallback is `END − max(1, event_count) seconds`. Individual event timestamps are linearly interpolated across `[start, end]`. See [architecture.md — The tail translator](architecture.md#the-tail-translator) for details.

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `path` | positional | — | File or directory containing `cc_raw_output.jsonl` |
| `--run-id RUN_ID` | string | auto-derived | Explicit run ID (single-file ingestion only) |
| `--source-name SOURCE_NAME` | string | `basename(<path>)` | Human label written into `state.json` |
| `--dry-run` | flag | off | Parse and report event counts; write nothing |
| `--force` | flag | off | Re-ingest even if events already exist |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success, already ingested (no-op), or no files found |
| 1 | Unexpected I/O failure reading the source file |
| 2 | User error: path not found, or `--run-id` used with a multi-file directory |

### Examples

```bash
# Ingest a single file
agentlog tail ./logs/cc_raw_output.jsonl

# Ingest all cc_raw_output.jsonl files under a directory recursively
agentlog tail ./logs/

# Dry-run to see what would be written
agentlog tail ./logs/ --dry-run

# Force re-ingest (overwrites existing run data)
agentlog tail ./logs/cc_raw_output.jsonl --force

# Pin an explicit run ID for a single file
agentlog tail ./logs/cc_raw_output.jsonl --run-id my-run-2026-05-28

# Label the source in state.json
agentlog tail ./logs/ --source-name "nightly-refactor"
```

---

## agentlog ls

List captured runs across hooks and SDK sources.

### Synopsis

```
agentlog ls [--source {hooks,sdk,all}] [--since DURATION] [--sort COLUMN]
            [--reverse] [--limit N] [--json] [--reindex]
```

### Description

Queries the SQLite index at `~/.agentlog/index.sqlite3` after refreshing any stale or missing rows from `runs/<id>/state.json`. The index is a derived cache — the JSON files are the source of truth. See [architecture.md — The SQLite index](architecture.md#the-sqlite-index).

Output columns: `RUN ID`, `SOURCE`, `STARTED`, `DUR`, `EVENTS`, `TOKENS`, `MODEL`. Default sort is newest-first by `started_at`.

The `--since` duration grammar accepts: `s` (seconds), `m` (minutes), `h` (hours), `d` (days), `w` (weeks). Examples: `30m`, `24h`, `7d`. The same grammar is used by `agentlog cost --since`.

The `--sort cost` option is an alias for `--sort tokens` in v0.1; it will map to actual dollar-cost sorting in v0.2+.

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--source` | `hooks`/`sdk`/`all` | `all` | Filter by data source |
| `--since DURATION` | duration | — | Only runs started within DURATION |
| `--sort` | `started`/`ended`/`duration`/`events`/`tokens`/`cost` | `started` | Sort column |
| `--reverse` | flag | off | Ascending order (default is descending / newest-first) |
| `--limit N` | int | 50 | Max rows to show; `0` = unlimited |
| `--json` | flag | off | Machine-readable JSON output |
| `--reindex` | flag | off | Force a full SQLite index rebuild before listing |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including empty results) |
| 1 | SQLite or I/O failure |
| 2 | Invalid flag value (e.g., bad `--since` format) |

### Examples

```bash
# Default: all runs, newest first, up to 50 rows
agentlog ls

# Last 24 hours of hooks runs only
agentlog ls --source hooks --since 24h

# Sort by token count descending
agentlog ls --sort tokens

# All runs, no row limit, JSON output
agentlog ls --limit 0 --json

# Force a full index rebuild (useful after manual edits to runs/)
agentlog ls --reindex

# Oldest first
agentlog ls --reverse
```

---

## agentlog cost

Show token counts and dollar cost for one run or all runs.

### Synopsis

```
agentlog cost [run_id] [--all] [--source {hooks,sdk,all}] [--since DURATION]
              [--pricing PATH] [--json] [--no-cache-cost]
```

### Description

Exactly one of `run_id` or `--all` must be provided; combining them exits 2.

For a single run, reads `runs/<run_id>/state.json` and `runs/<run_id>/cost.json` directly and prints a per-token-kind breakdown. For `--all`, walks every run directory matching the optional `--source` and `--since` filters and prints a cross-run rollup sorted by cost descending (unknown-cost runs at the bottom).

`--source` and `--since` are only valid with `--all`; using them with a positional `run_id` exits 2.

**Pricing override chain** (highest to lowest priority):

1. `--pricing PATH` — explicit file path; exits 2 if the file does not exist
2. `$AGENTLOG_PRICING` env var — path to override file; silently ignored if absent or missing
3. `$AGENTLOG_HOME/pricing.json` — per-installation override; silently ignored if absent
4. Built-in table (snapshot dated 2026-05-27)

Override merge semantics: per-model whole-row replacement. A user file entry for `claude-sonnet-4-6` replaces the entire built-in row for that model. Models absent from the user file inherit from the built-in. See [architecture.md — The pricing table](architecture.md#the-pricing-table).

`--no-cache-cost` excludes `cache_creation` tokens from the cost total. It does **not** exclude `cache_read` tokens. Useful for isolating baseline (non-caching) costs.

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `run_id` | positional | — | Run ID to show cost for (mutually exclusive with `--all`) |
| `--all` | flag | off | Show cost rollup for all runs |
| `--source` | `hooks`/`sdk`/`all` | `all` | Filter by source (only with `--all`) |
| `--since DURATION` | duration | — | Only runs within DURATION (only with `--all`) |
| `--pricing PATH` | path | — | JSON pricing-override file (merged onto built-in) |
| `--json` | flag | off | Machine-readable JSON output |
| `--no-cache-cost` | flag | off | Exclude `cache_creation` cost from total (not `cache_read`) |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including runs with unknown model / unknown cost) |
| 1 | Unexpected I/O failure |
| 2 | User error: bad flag combination, missing `--pricing` file, or invalid `run_id` |

### Examples

```bash
# Single-run cost breakdown
agentlog cost sdk-abc123

# All runs, cost descending
agentlog cost --all

# Last 7 days of hooks runs
agentlog cost --all --source hooks --since 7d

# Use a custom pricing file
agentlog cost --all --pricing ~/my-pricing.json

# Exclude cache_creation from total (for debugging)
agentlog cost sdk-abc123 --no-cache-cost

# Machine-readable single-run output
agentlog cost sdk-abc123 --json

# Cross-run rollup as JSON
agentlog cost --all --json
```

---

## agentlog view

Render a single captured run as a three-panel TUI.

### Synopsis

```
agentlog view <run_id> [--limit N] [--events-only] [--no-truncate] [--json]
```

### Description

Displays a captured run in three panels:

- **Header** — run ID, source, model, started, duration, cwd
- **Timeline** — chronological event list with ASCII rail decorations, color-coded by event kind
- **Cost footer** — token counts and dollar cost (same computation as `agentlog cost`)

Requires the `rich` library for TUI rendering (installed via the `[tui]` extra — see [getting-started.md § Install](getting-started.md#1-install)). The `--json` flag emits a combined JSON object and works without `rich` installed.

`--limit` caps the number of timeline events shown. Use `--limit 0` to show all events. The header and cost footer are always shown unless `--events-only` is passed.

`--no-truncate` disables the 80-character (tool params) and 60-character (tool results) per-row display cap. Useful for inspecting full content but can produce very wide output.

Cost computation in the footer uses the same pricing logic as `agentlog cost`; the pricing override chain (`--pricing`, `$AGENTLOG_PRICING`, `$AGENTLOG_HOME/pricing.json`, built-in) is not exposed here — use `agentlog cost` for override control.

### Flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `run_id` | positional | — | Run ID to inspect (from `agentlog ls`) |
| `--limit N` | int | 100 | Max timeline events to show; `0` = unlimited |
| `--events-only` | flag | off | Render only the timeline section (skip header and cost footer) |
| `--no-truncate` | flag | off | Disable 80/60-char per-row display cap |
| `--json` | flag | off | Emit combined JSON object; works without `rich` installed |

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | `rich` is not installed (non-JSON output only), or I/O failure reading run data |
| 2 | User error: invalid `run_id`, run directory not found |

### Examples

```bash
# Render a run (requires rich)
agentlog view sdk-abc123

# Show only the first 20 events
agentlog view sdk-abc123 --limit 20

# Events only — no header or cost footer
agentlog view sdk-abc123 --events-only

# Full content, no truncation
agentlog view sdk-abc123 --no-truncate

# JSON output (works without rich)
agentlog view sdk-abc123 --json

# Pipe JSON for further processing
agentlog view sdk-abc123 --json | jq '.events[] | select(.event == "tool_use")'
```

---

## The `_hook` subcommand

`agentlog _hook` is the internal routing target written into `~/.claude/settings.json` by `agentlog init`. It is suppressed from `--help` output (see `cli.py:220–225`) and should not be invoked manually.

When Claude Code fires a hook event, it runs a command of the form:

```
agentlog _hook SessionStart
agentlog _hook UserPromptSubmit
agentlog _hook PostToolUse
agentlog _hook Stop
agentlog _hook SessionEnd
```

Each call reads the event payload from stdin, dispatches to the appropriate recorder in `capture.py`, and exits 0 unconditionally (fail-open per CLAUDE.md hard rule #2). If you see `agentlog _hook` in a `ps` listing or a shell trace, it is the hook handler running normally.
