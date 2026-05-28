# Architecture

How agentlog works under the hood. For the project's design decisions and rationale, see [DESIGN.md](../DESIGN.md). For the non-negotiable v0.1 constraints, see [CLAUDE.md](../CLAUDE.md).

---

## Two ingest paths, one schema

Every captured run lands in the same `runs/<id>/` directory layout regardless of how it was captured.

```
┌─────────────────────────────────────────────────────┐
│  Ingest path 1: Claude Code hooks                   │
│                                                     │
│  claude (interactive session)                       │
│    │                                                │
│    ├─ SessionStart  ──────────────────────────┐     │
│    ├─ UserPromptSubmit ──────────────────────┐│     │
│    ├─ PostToolUse ──────────────────────────┐││     │
│    ├─ Stop ─────────────────────────────────┤││     │
│    └─ SessionEnd ───────────────────────────┘││     │
│                   agentlog _hook <Event>  ───┘│     │
│                   (capture.run_hook)  ────────┘     │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
             ~/.agentlog/runs/<id>/
               ├── state.json
               ├── events.jsonl
               └── cost.json
                        ▲
┌───────────────────────┘─────────────────────────────┐
│  Ingest path 2: SDK sidecar (tail)                  │
│                                                     │
│  cc_raw_output.jsonl  ──▶  _RECORD_TRANSLATORS      │
│  (stream-json output       (tail._translate)        │
│   from claude-code-sdk      ↓                       │
│   or Anthropic SDK)        translate → write        │
└─────────────────────────────────────────────────────┘
```

The unified schema is the load-bearing v0.1 decision: `agentlog ls`, `agentlog cost`, and `agentlog view` work identically on hooks runs and SDK-ingested runs because both sources produce the same `state.json` / `events.jsonl` / `cost.json` format. The `source` field (`"hooks"` or `"sdk"`) is the only discriminator; everything downstream is source-agnostic.

---

## The `runs/<id>/` directory layout

Each run lives under `$AGENTLOG_HOME/runs/<id>/`. Constants from `src/agentlog/_constants.py` are named in **bold**.

### Per-run files

| File | Written by | Key fields |
|---|---|---|
| `state.json` | `capture._on_session_start` (initial), `capture._on_session_end` (final); `tail._process_one` | `schema_version` (**SCHEMA_VERSION**=1), `session_id`, `parent_session_id`, `started_at`, `ended_at`, `cwd`, `model`, `event_count`, `source` (**SOURCE_HOOKS**=`"hooks"` or **SOURCE_SDK**=`"sdk"`), `summary` |
| `events.jsonl` | `capture._append_event` (per hook event); `tail._append_event` (per translated record) | One JSON object per line; every record carries `schema_version`, `event`, `timestamp`, `session_id`, `source`, plus event-specific fields. Append-only. |
| `cost.json` | `capture._on_stop` (incremental, per Stop event); `tail._process_one` (one-shot, per SDK file) | `schema_version`, `session_id`, `totals` (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`), `phases` (populated in v0.2+) |
| `_logs/` | — | Reserved; not written in v0.1 |

### Global artifacts

| Path | Written by | Role |
|---|---|---|
| `~/.agentlog/_self.log` (**SELF_LOG_NAME**) | `capture._log_self`, `tail._log_self`, `ls._log_self` | Fail-open landing zone; append-only, timestamped. Never raises. |
| `~/.agentlog/index.sqlite3` (**INDEX_FILE_NAME**) | `ls._refresh_index` | SQLite cache for `agentlog ls` queries. NOT the source of truth — the JSON files are. |
| `~/.agentlog/pricing.json` (**PRICING_FILE_NAME**) | User-managed | Optional per-installation pricing override. |

---

## The hook handlers

Five v0.1 hook events are captured. `PreToolUse` is **intentionally absent** — blocking hooks are the highest-risk surface in Claude Code's hot path. Trust is built with logging first; `PreToolUse` is deferred to v0.2+ per CLAUDE.md hard rule #5.

| Hook event | Recorder | What is written |
|---|---|---|
| `SessionStart` | `capture._on_session_start` | Creates `state.json` with initial metadata; appends `session_start` event to `events.jsonl` |
| `UserPromptSubmit` | `capture._on_user_prompt_submit` | Appends `prompt` event; text truncated to **MAX_INLINE_BYTES**=4096 bytes if larger |
| `PostToolUse` | `capture._on_post_tool_use` | Appends `tool_use` event; `params_summary` and `result_summary` each truncated to **MAX_INLINE_BYTES** |
| `Stop` | `capture._on_stop` | Appends `stop` event with `usage` (token counts); incrementally updates `cost.json` |
| `SessionEnd` | `capture._on_session_end` | Appends `session_end` event; finalises `state.json` with `ended_at`, `event_count`, `summary` |

Unknown events (Anthropic hook payload schema drift) are recorded as `event="unknown"` with the raw payload — the handler never crashes on unrecognised input (CLAUDE.md hard rule #7: schema versioned, fail on unknown → log raw).

**Performance contract** (CLAUDE.md hard rules #1 and #2):

- Steady-state budget: **<10ms** per handler call
- Cold-start budget: **<50ms**
- No network calls in any handler — ever
- `run_hook` exits 0 unconditionally; a buggy handler MUST NOT break a Claude Code session

The fail-open boundary in `capture.run_hook` wraps the entire call in `try/except Exception`. Critically, even the self-logging recovery path (`_log_self`) is wrapped in `contextlib.suppress(Exception)` — if `~/.agentlog/` is read-only, the logging call itself cannot propagate an exception. The exit path is unconditional `return 0` (ADW lesson #7).

---

## The tail translator

`tail._RECORD_TRANSLATORS` is the per-record-type dispatch dict that translates stream-json records from `cc_raw_output.jsonl` into agentlog's unified event schema. The dict maps record `type` strings (`"system"`, `"assistant"`, `"user"`, `"result"`) to translator functions; records with unknown types fall through to `_translate_unknown`, which records them as `event="unknown"` with the raw payload — the same graceful drift handling as the hook path. `_RECORD_TRANSLATORS` is a load-bearing identifier: treat it as part of the durable installed format (ADW lesson #5).

Stream-json records do not carry per-event timestamps. agentlog derives timestamps using a three-step strategy:

1. **END = file mtime.** Claude Code streams output as the session runs; the last write sets the mtime, which approximates the session end time.
2. **START = END − `result.duration_ms`.** The `result` record (final record in a complete stream) carries the authoritative session duration. If no `result` record is present (truncated or errored session), the fallback is `END − max(1, event_count) seconds`.
3. **Per-event timestamps: linear interpolation.** All events are spread uniformly across `[start, end]`, preserving relative order and anchoring the visible timeline to the real session window. No per-event times exist in the source data; interpolation is the most honest representation — it neither collapses all timestamps to a single point nor invents precision.

`--run-id` is only valid for single-file ingestion. Passing it with a directory containing multiple files exits 2 to prevent accidental run-ID collisions.

---

## The SQLite index

`~/.agentlog/index.sqlite3` is a **derived cache** — it is never the source of truth. The JSON files under `runs/` are authoritative. If the index is deleted, the next `agentlog ls` rebuilds it from scratch.

**Refresh-on-stale algorithm:**

1. Walk every `runs/<id>/` directory, compute the current `state.json` mtime and `cost.json` mtime.
2. For each run, fetch the stored mtimes from the `runs` table. If they match, skip (no I/O on the JSON files). If they differ or the row is absent, re-read `state.json` and `cost.json` and upsert.
3. Delete rows for run IDs that no longer exist on disk.

**Schema-version bootstrap order** (ADW lesson #3 — order matters):

1. `_ensure_schema_version_table` — creates the `schema_version` table. Its shape never changes; it is safe to create before checking the version.
2. `_check_schema_version` — reads the stored **INDEX_SCHEMA_VERSION** (currently 1). If absent or mismatched, drops the `runs` table and resets the version row. This guarantees the next step runs against a clean slate.
3. `_init_schema` — creates the `runs` table and indexes. Always runs against the current schema.

The recovery path is exercised by `tests/test_ls.py::test_future_schema_version_drops_and_rebuilds_runs_table`: seed an index file with `version=999`, run `agentlog ls`, assert the table is rebuilt cleanly.

---

## The pricing table

`agentlog cost` resolves the pricing table from the following chain, highest priority first:

| Priority | Source | Behaviour on absent/invalid |
|---|---|---|
| 1 | `--pricing PATH` flag | Exits 2 if file does not exist |
| 2 | `$AGENTLOG_PRICING` env var | Silently ignored if unset or path absent |
| 3 | `$AGENTLOG_HOME/pricing.json` | Silently ignored if absent |
| 4 | Built-in table | Always present; snapshot dated 2026-05-27 |

**Merge semantics:** per-model whole-row replacement. If a user file contains an entry for `claude-sonnet-4-6`, the entire built-in row for that model is replaced. Models absent from the user file inherit from the built-in. Missing `input`/`output`/`cache_read`/`cache_creation` kinds within a user entry default to `0.0` and are logged to `_self.log`.

The built-in table covers `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, and `claude-haiku-4-5`. Runs on unlisted models display `??` for cost and report `cost_unknown_reason: "model not in pricing table"`. Anthropic pricing changes over time; the snapshot will go stale and the operator owns refreshing it.

---

## What's deliberately out of scope

See [DESIGN.md — Explicit non-goals for v0.1](../DESIGN.md) for the full rationale behind each exclusion.

| Feature | Status |
|---|---|
| `PreToolUse` hook | v0.2+ — blocking hooks deferred (CLAUDE.md rule #5) |
| Cost-budget kill-switch | v0.2+ — too complex, too high risk for v0.1 |
| Native Python API (`with agentlog.run(...)`) | v0.2+ |
| Subprocess wrapper (`agentlog.subprocess(...)`) | v0.2+ |
| `agentlog diff <a> <b>` | v0.2+ |
| `agentlog replay <id>` (beyond basic event streaming) | v0.2+ |
| OTEL exporter | v1.0+ |
| Web dashboard | Never — local-first principle (CLAUDE.md rule #6) |

---

## For contributors

All recurring patterns surfaced during the v0.1 build are catalogued in [docs/adw-lessons.md](adw-lessons.md). Read it before touching any of the following: sort ordering, SQLite schema changes, installed-format constants, fail-open boundaries, or dispatch tables. The lessons include exact test shapes to copy.

Any new feature should follow the same SDLC pipeline: write a spec prompt, run `.adw/travis/travis_sdlc.py` against it, and produce a `docs/feature-*.md` + `specs/feature-*.md` pair before implementation.

Test coverage lives in:

```
tests/test_capture.py       — dispatch table, per-event recorders, fail-open boundary
tests/test_cli_smoke.py     — end-to-end subcommand invocations, exit codes
tests/test_cost.py          — pricing resolution, token math, unknown-model handling
tests/test_handler_perf.py  — <10ms steady-state budget per hook handler
tests/test_hooks_install.py — idempotent merge, preservation of foreign hooks
tests/test_ls.py            — SQLite bootstrap, refresh-on-stale, sort ordering
tests/test_tail.py          — timestamp derivation, _RECORD_TRANSLATORS dispatch
tests/test_view.py          — three-panel layout, JSON output, --events-only
```

Named regression tests exist for every reviewer-confirmed bug (ADW lesson #11). If you fix a new bug, the same commit should add a `test_<feature>_<scenario>_<observable_property>` test that would have caught it.
