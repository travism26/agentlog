# Research: `agentlog cost` — per-run + cross-run token-to-dollar rollup

## Metadata

adw_id: `355ec9b6`
prompt: `/tmp/agentlog_step5_prompt.md` — implement v0.1 ship-scope item #5: `agentlog cost <id>` plus `--all` rollup. Reads `runs/<id>/cost.json` and `state.json`, multiplies by a built-in (overridable) per-model pricing table, prints a per-token-kind breakdown with a grand total. Adds module `src/agentlog/cost.py`, pulls `"cost"` out of `_STUB_SUBCOMMANDS` in `cli.py`, no new runtime deps.
date: `2026-05-27`

## Executive Summary

Item #5 is the dollar-amount payoff that turns the "$6,000 Claude bill" pain from `DESIGN.md` into a number the user can actually act on. The data is already on disk — `cost.json::totals` carries the four token kinds, `state.json::model` carries the model id. This is a read-only, stdlib-only calculation: walk the runs tree (same way `ls` does, **without** touching the SQLite index — `cost` is independent of `ls` per the prompt's hard rules), parse the small JSON files, multiply by a per-million-token pricing table, and format. New module `src/agentlog/cost.py` (~400-500 lines), one inline subparser branch in `cli.py`, one new `PRICING_FILE_NAME` constant in `_constants.py`, one new `tests/test_cost.py`. Zero changes to `capture.py`, `tail.py`, `hooks_install.py`, `ls.py`, `pyproject.toml`.

## Existing Architecture

### Relevant Documentation Found

| Path | Purpose for this task |
|---|---|
| `DESIGN.md` ship-scope table (line 194) | `agentlog cost <id>` — 1-2 days — "Parse `usage` blocks; multiply by model pricing table." Confirms scope. |
| `DESIGN.md` lines 61-71 | Use cases #1 + #3: `--since 8h --sort cost` ("show me what they cost overnight") and `agentlog cost <id>` ("how much did this chat just cost?"). Both motivate the per-run and `--all` shapes. |
| `DESIGN.md` open question #3 (line 317) | "Model pricing table source — hardcoded? fetched from Anthropic? user-provided JSON?" — prompt resolves: hardcoded built-in fallback, user JSON override via `--pricing` / `$AGENTLOG_PRICING` / `$AGENTLOG_HOME/pricing.json`, **NO** network fetch. |
| `DESIGN.md` non-goals (line 204) | "Cost-budget kill-switch / `--max-spend $X` — explicitly cut per operator direction." Reinforced by prompt's "Out of scope" section: do NOT add `--limit-spend` or similar. |
| `DESIGN.md` v0.2+ deferrals (lines 207-214) | Per-phase breakdown, cost-over-time charts, pricing-table auto-update — all deferred. Mirrored in prompt. |
| `CLAUDE.md` hard rule #4 | "No cost-budget kill-switch in v0.1." Belt-and-suspenders confirmation. |
| `CLAUDE.md` hard rule #6 | "Local-first. No SaaS, no required network calls." Pricing comes from a static table or user file — never anthropic.com at runtime. |
| `CLAUDE.md` hard rule #7 | Schema versioned. `cost.json` carries `schema_version: 1`. New code should tolerate version mismatch (log, don't crash). |
| `ai_docs/research/07ec0bb6-ls-unified-view-analysis.md` | Prior research doc for `ls`. Establishes the same fail-loud, stdlib-only, helper-duplication pattern that `cost` will mirror. Documents the `state.json` + `cost.json` shape `cost` will read (intersection of hooks-mode and SDK-mode). |
| `ai_docs/research/fabf1d0d-hook-handlers-capture-analysis.md` | Hooks research — confirms `_on_stop` writes `cost.json::totals` with the four token keys (`input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens`) that the new module multiplies. |
| `ai_docs/research/0241d756-tail-sdk-sidecar-analysis.md` | SDK sidecar research — confirms `tail.run_tail` writes the identical `cost.json` shape, so `cost` reads a uniform schema across both sources. |

### Component Map

```
            ┌─────────────────────────────────────────────────────┐
            │  $AGENTLOG_HOME/runs/                               │
            │   ├─ <session-id>/    (hooks-mode, capture.py)      │
            │   │   ├─ state.json   {schema_version, session_id,  │
            │   │   │                source, model, started_at,   │
            │   │   │                ended_at, ...}               │
            │   │   └─ cost.json    {totals:{input_tokens, ...},  │
            │   │                    phases:{}}                   │
            │   └─ sdk-<id>/        (SDK-mode, tail.py)           │
            │       ├─ state.json   (superset; same fields)       │
            │       └─ cost.json    (identical shape)             │
            └────────────────────────┬────────────────────────────┘
                                     │  READ-ONLY (cost MUST NOT
                                     │  mutate state.json, events,
                                     │  cost.json — prompt hard rule)
                                     ▼
            ┌─────────────────────────────────────────────────────┐
            │  src/agentlog/cost.py (NEW)                         │
            │    BUILTIN_PRICING_PER_MILLION  (constants block)   │
            │    _resolve_pricing(--pricing flag, env, home)      │
            │    _load_pricing_file(path) -> (table, source_tag)  │
            │    _merge_pricing(builtin, user) -> merged table    │
            │    _compute_run_cost(state, cost, pricing)          │
            │    _format_single(...) / _format_all(...)           │
            │    _format_json_single(...) / _format_json_all(...) │
            │    run_cost(args) -> int                            │
            └────────────────────────┬────────────────────────────┘
                                     │ (NEVER reads
                                     │  index.sqlite3 — independent
                                     │  of ls.py per prompt)
                                     ▼
            ┌─────────────────────────────────────────────────────┐
            │  Optional:                                          │
            │   $AGENTLOG_HOME/pricing.json (user-curated)        │
            │   $AGENTLOG_PRICING        (env override)           │
            │   --pricing <path>         (flag override)          │
            └─────────────────────────────────────────────────────┘

            CLI wiring:
              src/agentlog/cli.py
                _STUB_SUBCOMMANDS -= {"cost"}
                new `elif name == "cost":` branch
                _run_cost(args) -> cost.run_cost(...)
```

### Key Files and Modules

| File | Why it matters |
|---|---|
| `src/agentlog/cli.py:12-14` | `SUBCOMMANDS` already lists `"cost"`. `_STUB_SUBCOMMANDS = frozenset({"cost", "view"})` — remove `"cost"`. |
| `src/agentlog/cli.py:25-133` | The `for name in SUBCOMMANDS:` loop. Add `elif name == "cost":` branch mirroring the `ls` branch (lines 88-133): create subparser, add all flags (positional `run_id` optional, `--all`, `--source`, `--since`, `--pricing`, `--json`, `--no-cache-cost`), `sp.set_defaults(func=_run_cost)`. Add `_run_cost(args)` thin shim below `_run_ls` (~line 167). |
| `src/agentlog/cli.py:10` | Import update: `from agentlog import __version__, capture, cost, hooks_install, ls, tail`. |
| `src/agentlog/_constants.py:36-42` | Add `PRICING_FILE_NAME: str = "pricing.json"` after the existing `INDEX_FILE_NAME` constant. Keep ordering: data-root family together. |
| `src/agentlog/ls.py:44-48` | `_data_root()` definition — duplicate verbatim in `cost.py` per the established "fail-loud CLIs duplicate helpers" convention. Alternative: import `ls._data_root` (the prompt says "Reuse helpers if clean: ... `ls._resolve_data_root` (or equivalent)" — but `ls` exports a private `_data_root`, so importing it is a leaky abstraction. Recommend duplicating; matches `tail.py`/`capture.py`/`ls.py` precedent.) |
| `src/agentlog/ls.py:272-285` | `_parse_duration` is exposed in `ls.py` (not private — used by `cli.py`'s `--since` argparse type). The prompt says "Reuse helpers if clean: `ls._parse_duration` for `--since`." Importing `ls._parse_duration` is fine — it's a leaf utility with no `ls`-specific state. Alternative: copy it. Recommend reuse (single source of truth for the `30m|24h|7d` grammar). |
| `src/agentlog/ls.py:51-59` | `_log_self` — duplicate verbatim. Same justification as `_data_root`. |
| `src/agentlog/ls.py:337-371` | `_format_duration(start_iso, end_iso)` and `_started_display(started_at)` — duration formatter for `Duration: 3h56m24s` and `Started: 2026-05-27T08:51:43Z` in the single-run header. Prompt's example output uses identical formatting to `ls`. Recommend importing both from `ls.py` (they're not private to `ls`'s SQLite logic; they're pure formatters). Acceptable alternatives: duplicate, or factor to a `_format.py` helper module. Use judgment per prompt. |
| `src/agentlog/_constants.py:36-37` | `DEFAULT_DATA_ROOT_NAME = ".agentlog"`, `RUNS_DIR_NAME = "runs"`. Reused. |
| `src/agentlog/_constants.py:38` | `SELF_LOG_NAME = "_self.log"`. Used by duplicated `_log_self`. |
| `src/agentlog/_constants.py:32-34` | `SOURCE_HOOKS = "hooks"`, `SOURCE_SDK = "sdk"`. Used for `--source` filter (also the `state.json::source` value). |
| `src/agentlog/capture.py:264-281` | Source of truth for hooks-mode `cost.json` shape — confirms `totals: {input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}` are the four kinds to multiply. Empty `phases: {}` (prompt: ignore in v0.1). |
| `src/agentlog/tail.py:361-366, 443-451` | Source of truth for SDK-mode `cost.json` shape — identical to hooks-mode. The reader is uniform across sources. |
| `src/agentlog/capture.py:130-156` | Hooks-mode `state.json` writes `source: "hooks"` and includes `model` (from `SessionStart` payload). `cost` reads `state["model"]` to pick the pricing row. |
| `src/agentlog/tail.py:345-360` | SDK-mode `state.json` writes `source: "sdk"` and includes `model` (populated from the first `session_start` event in `cc_raw_output.jsonl`). |
| `pyproject.toml:28` | `dependencies = []` MUST stay empty. Confirmed by prompt acceptance criteria. |
| `pyproject.toml:80-84` | mypy strict on `src` and `tests`. All `cost.py` signatures fully typed. `dict[str, dict[str, float]]` for pricing tables. `argparse.Namespace` for the args shim. |
| `pyproject.toml:64-78` | ruff rules: `E/W/F/I/B/UP/SIM`. `from __future__ import annotations` at top. PEP 604 unions (`str | None`). |
| `tests/test_cli_smoke.py:25-32` | Currently excludes `{"init", "uninstall", "tail", "ls"}` from the "not yet implemented" parametrised test. Extend to include `"cost"`. |
| `tests/test_ls.py:27-78` | Reference for seeding test runs: `_seed_sdk_run(tmp_path, run_id, ...)` via `tail.run_tail` against `tests/fixtures/sdk_minimal.jsonl`; `_seed_hooks_run(tmp_path, session_id, ...)` via `capture.dispatch`. Reuse the same pattern in `tests/test_cost.py`. |
| `tests/fixtures/sdk_minimal.jsonl` | Existing SDK fixture used to seed runs in tests. Each run produced via `tail.run_tail` has `cost.json` and `state.json` populated — exactly what `cost` consumes. |
| `~/.agentlog/runs/sdk-*/state.json` (local data) | Real-world examples on operator's machine; current ADW runs use `claude-opus-4-7` and `claude-sonnet-4-6`. These are the two model ids the built-in pricing table MUST cover. |

### Schema fields actually consumed by `cost`

From `state.json` (intersection across hooks-mode + SDK-mode):

```
session_id     -> displayed as "Run:"   (also derived from dir name)
source         -> displayed as "Source:"  (also --source filter)
model          -> pricing-table key      (unknown-model path if absent/typo)
started_at     -> displayed as "Started:"  (also --since filter)
ended_at       -> displayed as part of "Duration:"
```

From `cost.json::totals`:

```
input_tokens
output_tokens
cache_read_tokens
cache_creation_tokens
```

All other fields ignored. `cost.json::phases` is read-tolerated but ignored in v0.1 (prompt: "the cost.json::phases field is reserved but stays empty in v0.1. Don't compute per-phase costs even if the field is populated").

## Affected Areas

### Files That Will Need Changes

| File | Change |
|---|---|
| `src/agentlog/cost.py` | **NEW**. Module docstring with pinned invariants (read-only re: runs/, local-first, stdlib-only, fail-loud user CLI, no SQLite-index dependency, no per-phase math in v0.1). Public surface: `run_cost(*, run_id, all_, source, since, pricing_path, as_json, no_cache_cost) -> int`. Internals: pricing resolution (`_resolve_pricing` returns `(table, source_tag)`); pricing-file loader (`_load_pricing_file` — validates gently, logs to `_self.log` on bad entries, returns the partial table); merger (`_merge_pricing`); per-run computation (`_compute_run_cost`); plain-table formatters (single-run + `--all`); JSON formatters (single-run + `--all`); CLI-arg validation and mutual-exclusion check between `run_id` and `--all`. ~400-500 lines including types. |
| `src/agentlog/cli.py` | Remove `"cost"` from `_STUB_SUBCOMMANDS` (line 14). Add new `elif name == "cost":` branch in the build-parser loop (insert after the `ls` branch, ~line 133). Add `_run_cost(args)` helper below `_run_ls` (~line 167). Update top-level import to add `cost`. |
| `src/agentlog/_constants.py` | Add `PRICING_FILE_NAME: str = "pricing.json"` after `INDEX_FILE_NAME` (line 39). |
| `tests/test_cli_smoke.py` | Extend the "not yet implemented" exclusion set from `{"init", "uninstall", "tail", "ls"}` to `{"init", "uninstall", "tail", "ls", "cost"}`. |
| `tests/test_cost.py` | **NEW**. ~25-35 test functions covering acceptance criteria: single-run plain output, single-run --json, `--all` rollup with grand total, default cost-desc sort, `--source` filter, `--since` filter, mutual exclusion (run_id + --all), missing run id (rc=2), missing cost.json (zeros + rc=0), missing model field (unknown-model path), pricing-file flag (path nonexistent → rc=2), pricing-file invalid JSON (rc=2), pricing-file with one model overriding builtin (merge semantics), pricing-file with custom model (merge semantics), pricing-file with missing kind (defaults to 0.0 + _self.log line), pricing-file with negative price (defaults to 0.0 + _self.log line), env-var pricing path, $AGENTLOG_HOME/pricing.json auto-discovery, `--no-cache-cost` excludes cache-creation, all-zero-usage run, footer note appears on `--all` output, footer note appears on unknown-model output, `pricing_source` reported in JSON. |

### Files explicitly NOT changing

- `src/agentlog/capture.py` — `cost` is read-only; hook path untouched.
- `src/agentlog/tail.py` — same; SDK ingest writes the data `cost` reads.
- `src/agentlog/ls.py` — `cost` may *import* `_parse_duration` and the two display formatters from it, but does not modify it. The SQLite index is untouched.
- `src/agentlog/hooks_install.py` — unrelated.
- `pyproject.toml` — `dependencies = []` stays empty (stdlib-only).
- `README.md` — explicitly out of scope per prompt; docs phase handles `docs/feature-355ec9b6-cost-rollup.md`.

### Dependencies

**Inbound** (what depends on the new module): only `cli.py`. Module is leaf-positioned in the import graph (mirrors `tail.py`, `ls.py`, `hooks_install.py`).

**Outbound** (what `cost.py` depends on):

- stdlib: `argparse, json, os, sys, re, contextlib, datetime, pathlib, typing` (and possibly `decimal` if we choose `Decimal` for money math — see Risks #1 below).
- `agentlog._constants` — for `DEFAULT_DATA_ROOT_NAME, RUNS_DIR_NAME, SELF_LOG_NAME, SOURCE_HOOKS, SOURCE_SDK`, plus the new `PRICING_FILE_NAME`.
- `agentlog.ls` — for `_parse_duration` (and optionally `_format_duration`, `_started_display`). Importing these is acceptable per prompt; alternative is duplication.
- NO import of `capture` or `tail` (independent of write-side code).
- NO import of `sqlite3` (independent of the SQLite index from item #4).
- Optional, lazy-imported: NONE for v0.1. `rich` is NOT used by `cost` — the prompt's output examples are plain-text fixed-width tables. (Adding `rich` later for `cost` is a v0.2 polish item; keep `cost.py` rich-free for now to keep tests simple.)

### Integration Points

1. **Data root resolution.** `_data_root()` reads `os.environ["AGENTLOG_HOME"]` with fallback to `Path.home() / DEFAULT_DATA_ROOT_NAME`. Identical to `capture.py`, `tail.py`, `ls.py`. All four modules must agree (test monkeypatches `AGENTLOG_HOME`).
2. **Runs walk.** `_data_root() / RUNS_DIR_NAME` iterated with `Path.iterdir()` for `--all`. Each entry that is a directory containing `state.json` becomes one row. Run dirs without `state.json` are silently skipped (mid-write or crashed run — same convention as `ls`).
3. **Single-run lookup.** `_data_root() / RUNS_DIR_NAME / <run_id> / state.json` and `.../cost.json`. If `state.json` missing → rc=2 with `run id '<X>' not found at <path>` (the run dir or its state file doesn't exist). If `cost.json` missing → treat tokens as 0, rc=0 (the run existed, it just didn't produce usage data).
4. **`_self.log`.** Validation errors in user pricing files (missing kinds, negative prices, non-numeric values) append to `_data_root() / SELF_LOG_NAME` via duplicated `_log_self`. Shared log file with capture/tail/ls, no shared writer.
5. **Pricing-file resolution order** (highest → lowest priority):
   1. `--pricing PATH` CLI flag
   2. `$AGENTLOG_PRICING` env var
   3. `$AGENTLOG_HOME/pricing.json` (resolves through `_data_root()`)
   4. `BUILTIN_PRICING_PER_MILLION` module constant
   The first three are **merge** sources (user table is layered on top of the builtin; user keys win on collision). The fourth is the fallback when no user file is found.
6. **`pricing_source` tag**. JSON output includes one of `"builtin"`, `"file:<absolute-path>"`, `"missing"`. The tag is per-run (not per-invocation) so that a `--pricing` file covering only some models gives correct provenance per row.

## Impact Analysis

### Scope of Change

Small-to-medium. One new module (~400-500 lines), one new test file (~400-500 lines), ~6 lines of edits to `cli.py`, 1 line to `_constants.py`, 1 line to `test_cli_smoke.py`. No public-API changes outside the new subcommand surface. No on-disk format changes to `runs/<id>/*` — `cost` is a pure reader.

Strictly additive: if `cost.py` were deleted tomorrow, the rest of the system would still work (capture + tail + ls all continue to function; only the dollar-rollup view disappears).

### Risks and Considerations

1. **Float-precision money math.** Multiplying tokens by `rate / 1_000_000.0` gives results like `0.04498...` that round to `$0.0450`. Float arithmetic is safe at v0.1 scale (< $10,000 per row, sub-cent precision) — IEEE-754 double has ~15.95 decimal digits of precision; we display 4. Using `decimal.Decimal` would be technically more correct but adds verbosity for no v0.1 user-visible benefit. **Recommendation**: stick with floats; format with `f"${value:.4f}"`. If a future user reports a 1-cent discrepancy on a billion-token run, revisit then.
2. **Sub-cent precision matters for the user.** Prompt: "Dollar values to 4 decimal places (so sub-cent runs are still legible)." Don't truncate to 2dp. Don't use locale-aware `:,.2f` formatting — output may be pasted into bug reports (prompt note on UX).
3. **`--all` sort stability.** Default sort is cost-desc. Two runs with identical cost need a deterministic tiebreaker (recommend `started_at` desc as secondary). Otherwise the output is non-deterministic across invocations, which kills snapshot-style tests and confuses users.
4. **Unknown-model path is not an error.** Prompt is clear: rc=0 for unknown models. The user can fix by supplying a pricing file. Single-run output prints `??` rates + `??` cost rows but still shows token counts. JSON output omits `cost_usd` and `rates_per_million_usd`, sets `pricing_source: "missing"` and `cost_unknown_reason: "model not in pricing table"`. `--all` rollup includes unknown-cost runs in the per-run list with `cost: null` and adds `unknown_cost_runs: N` to the summary.
5. **Pricing-file validation — gentle, not strict.** Per prompt:
   - File path nonexistent (passed via `--pricing`) → **rc=2** before any computation (this one IS strict — user explicitly asked for a file that doesn't exist).
   - Invalid JSON → rc=2 with `invalid JSON in pricing file <path>`.
   - Model entry missing one of the four keys → use `0.0` for the missing kind, log `_self.log` line, continue.
   - Negative or non-numeric prices → use `0.0`, log to `_self.log`, continue.
   - Extra unknown keys (e.g., a typo'd `"inputt"`) → silently ignored; the four kinds are positionally consulted.
   The asymmetry between path-not-found (hard fail) and bad content (soft fail) matches the prompt verbatim and reflects the difference between "user gave us a clear command we can't fulfill" and "user gave us a file we can mostly use."
6. **`--no-cache-cost` semantics.** Prompt: "exclude cache-creation cost from totals (debugging)." Read carefully: this excludes **cache_creation** specifically, not all cache-related cost. `cache_read` continues to be charged. Document in the help text precisely; otherwise users will assume it strips all cache cost and report a "bug." Internally: set `cache_creation` rate or token count to 0 before summing.
7. **`--since` against `--all` only.** Prompt: "only meaningful with --all." Argparse cannot easily express "this flag is only valid when --all is set." Two approaches: (a) silently ignore `--since` when `run_id` is given (lossy — user thinks they filtered); (b) reject with rc=2 if `--since` is given without `--all`. Recommend (b) for clarity. Same for `--source`.
8. **`run_id` AND `--all` mutual exclusion.** Argparse doesn't natively express this for a positional + flag combo. Custom validation at the top of `run_cost` — if both given → rc=2 with `'<run_id>' and --all are mutually exclusive`. If neither given → rc=2 with usage message. The CLI subparser declares `run_id` as `nargs="?"` (optional positional) and `--all` as `action="store_true"`; mutual exclusion is enforced by hand.
9. **Pricing-table staleness footer.** Must appear on every `--all` output (not just unknown-model warnings). Prompt: "`pricing snapshot: built-in (2026-05-27). Override with --pricing <file> or $AGENTLOG_PRICING.`" This footer is suppressed in `--json` mode (JSON consumers don't need a chatty footer). For single-run output, the footer is shown only when the resolved pricing source is `"builtin"` (skip it if the user already overrode).
10. **Run-dir name vs `state.json::session_id`.** For SDK runs, the dir is named after the derived run-id (e.g., `sdk-c2974edc-...`) which equals `state.json::session_id` for SDK mode. For hooks mode, the dir name IS the session id. Display the dir name as `Run:` in the single-run header (prompt example uses `sdk-f50fb891-...` which matches the dir name). Internally, the dir name IS the `run_id`.
11. **Time format consistency.** Prompt: `Started: 2026-05-27T08:51:43Z` (seconds precision, `Z` suffix). Identical to `ls`'s `_started_display`. Reuse that helper. For `--json`, prompt says: "all timestamps stay in their full microsecond form from state.json" — pass through `state["started_at"]` verbatim without reformatting.
12. **Duration format.** Prompt: `Duration: 3h56m24s`. Identical to `ls`'s `_format_duration`. Reuse. For `--json`, also include `duration_seconds` as an integer.
13. **Run with `ended_at: null`.** A captured-but-not-yet-finished session (e.g., `SessionStart` fired, `SessionEnd` has not). `state.json::ended_at = None`. Display `Duration: -` (matches `ls` convention) and `duration_seconds: null` in JSON. Don't error.
14. **Empty runs dir for `--all`.** No runs found → exit 0 with `no runs found at <path>` (matches `ls`). For `--all --source hooks` against a tree with only SDK runs → exit 0 with `no runs match the filter` (prompt explicitly).
15. **Built-in pricing table layout.** Prompt specifies per-million-token values. Recommend computing `per_token = per_million / 1_000_000.0` once at module load (or via a helper) so the math at the call site is `tokens * rate_per_token`. Keep `BUILTIN_PRICING_PER_MILLION` as the human-readable source-of-truth dict, and derive a `BUILTIN_PRICING_PER_TOKEN` (or compute inline). Either is fine; prompt example does the math at call site with `* / 1M tokens` shown to the user — that's a display string, not necessarily the internal representation.
16. **No PyPI pricing leak.** Built-in pricing dates from 2026-05-27. README docs phase will document this. The module constant block should include the date comment inline (per prompt): `# $ per million tokens. Source: anthropic.com/pricing as of 2026-05-27.`
17. **Concurrent invocations.** Multiple `agentlog cost` running in parallel are fully read-only — no locking concerns. `_self.log` appends are atomic at byte boundaries on POSIX (writes < PIPE_BUF), and pricing-validation warnings are short single-line records. Worst case is interleaved log lines; not a correctness issue.
18. **Future `view` (item #6) overlap.** `view <id>` will likely show the cost block inline as part of its TUI. `cost` should expose a callable like `compute_run_cost(run_dir, pricing) -> dict` so `view` can reuse the computation without duplicating it. Keep `_compute_run_cost` reachable but module-private for v0.1; `view` can absorb the public-API question when item #6 lands.

### Existing Patterns to Follow

| Pattern | Where it shows up | How `cost` uses it |
|---|---|---|
| `from __future__ import annotations` | All `src/agentlog/*.py` | Same at top of `cost.py`. |
| `_data_root()` reads `AGENTLOG_HOME`, falls back to `~/.agentlog` | `capture.py:38-42`, `tail.py:52-56`, `ls.py:44-48` | Duplicate verbatim. |
| `_log_self(root, message)` — best-effort append to `_self.log` | `capture.py:74-82`, `tail.py:77-85`, `ls.py:51-59` | Duplicate verbatim for pricing-file warnings. |
| `argparse` subparser added via `for name in SUBCOMMANDS:` + `elif name == "...":` | `cli.py:25-133` | Same shape. |
| `_run_<command>(args)` thin shim → `run_<command>(...)` typed public function | `cli.py:148-167` | Same: `_run_cost(args)` → `cost.run_cost(...)`. |
| Public `run_<command>(...)` returns int (rc 0/1/2 with documented meanings) | `tail.py:462+`, `ls.py:468+` | Same. rc 0 = success, 1 = unexpected I/O failure, 2 = user error (bad run id, missing pricing file, mutual exclusion violation). |
| Module docstring opens with one-paragraph summary + numbered notes on invariants | `capture.py:1-9`, `tail.py:1-23`, `ls.py:1-15`, `hooks_install.py:1-21` | Same. Pin invariants: (a) read-only re: runs/ AND read-only re: index.sqlite3; (b) fail-loud user CLI; (c) stdlib-only; (d) local-first (no network); (e) per-phase math deferred to v0.2+; (f) pricing accuracy is the user's responsibility (built-in dates from 2026-05-27). |
| Stdlib-only public surface; no new runtime deps | `pyproject.toml:28` | `dependencies = []` stays empty; only stdlib imports. |
| Helper functions module-private (`_leading_underscore`); only `run_*` exported | All modules | Same in `cost.py`. Pricing constants (`BUILTIN_PRICING_PER_MILLION`) are module-level and may be referenced from tests without an underscore — they are intentionally public-by-convention so users (and future ADRs) can introspect. |
| Snake_case JSON keys | `ls.py:455` and prompt example | Same. `cost_usd`, `rates_per_million_usd`, `pricing_source`, `cost_unknown_reason`, `run_id`, `started_at`, `duration_seconds`, `total_tokens`. |
| Tests monkeypatch `AGENTLOG_HOME` → `tmp_path` | `test_capture.py`, `test_tail.py`, `test_ls.py:88-89` | Same in `test_cost.py`. |
| Seed test data via production functions (`tail.run_tail` against fixtures, `capture.dispatch` for hooks-mode) | `tests/test_ls.py:27-78` | Same. Add `_seed_unknown_model_run` helper for the unknown-model AC. |
| Fail-loud CLI: rc=2 for user error with clean stderr message | `tail.py:475-486`, `ls.py:516-519` | Same. Format: `agentlog cost: error: <message>`. |
| Plain table formatter uses `max(len(...))` per column for right-aligned widths | `ls.py:378-406` | Similar; columns are right-aligned for numeric (tokens, cost) and left-aligned for text (run id, model). |
| Module helpers duplicated rather than coupled when failure contracts diverge | Justified in `ls.py:5-15` and `tail.py:1-23` | Same for `cost.py` — `_data_root` and `_log_self` duplicated, `_parse_duration` imported from `ls.py` since it's a pure grammar parser with no module-specific state. |
| Use `Path` directly; avoid early `str()` conversion | All modules | Same. |
| JSON output via `json.dumps(payload, indent=2)` (single-shot, no streaming) | `ls.py:448-460` | Same. |

## Recommendations

### Implementation order

1. **`_constants.py` first.** Add `PRICING_FILE_NAME = "pricing.json"`. Single trivial commit.
2. **`cost.py` skeleton.** Module docstring with pinned invariants. Helpers duplicated from `ls.py` (`_data_root`, `_log_self`). Import `_parse_duration` from `ls.py`. Empty `run_cost(...)` returning 0. Type all signatures.
3. **`BUILTIN_PRICING_PER_MILLION` constant** with the five models from the prompt (`claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5`). Inline comment pinning the as-of date (2026-05-27).
4. **Pricing resolution layer.** `_load_pricing_file(path, root) -> dict[str, dict[str, float]]` parses + validates a single user file (returns partial table, logs bad entries). `_merge_pricing(builtin, user) -> dict` layers user on top of builtin. `_resolve_pricing(pricing_flag, root) -> tuple[dict, str]` walks the resolution order and returns (table, source_tag) where source_tag is `"builtin"` or `"file:<abs-path>"`.
5. **Per-run computation.** `_compute_run_cost(run_dir, pricing, no_cache_cost) -> dict` reads `state.json` and `cost.json`, looks up the model in the pricing table, computes per-kind cost and total. Returns a structured dict with all fields needed by both the plain and JSON formatters. On unknown model: returns the same structure with `cost_usd=None`, `pricing_source="missing"`. On missing `cost.json`: returns zeros.
6. **Single-run formatter (plain).** Function `_format_single_plain(record) -> str`. Header (`Run`, `Source`, `Model`, `Started`, `Duration`), then a 3-column table (Tokens, Rate, Cost), then footer. Right-align numeric columns. `??` for unknown-model rates and cost.
7. **`--all` formatter (plain).** Function `_format_all_plain(records, summary) -> str`. 4-column table (RUN ID, MODEL, TOKENS, COST), totals row, footer. Default sort: cost desc, started_at desc tiebreaker. Footer always shown.
8. **JSON formatters.** `_format_single_json(record) -> str` and `_format_all_json(records, summary) -> str`. Both via `json.dumps(payload, indent=2)`. Single-run output is a flat object; `--all` is `{"runs": [...], "summary": {...}}` per prompt.
9. **CLI wiring.** Remove `"cost"` from `_STUB_SUBCOMMANDS`. Add the new `elif name == "cost":` branch with all flags. Add `_run_cost` shim. Update `tests/test_cli_smoke.py`.
10. **Tests.** Write `tests/test_cost.py` covering every AC and edge case. Seed via `tail.run_tail` against `sdk_minimal.jsonl` plus a few hand-rolled state/cost fixtures for unknown-model and missing-cost cases.

### Architectural recommendations

- **Keep `cost.py` independent of `ls.py`'s SQLite layer.** Prompt is explicit: "It also MUST NOT touch the SQLite index from step 4 — `cost` is independent of `ls`." Walk the runs dir directly with `Path.iterdir()`. This is slower in theory than querying SQLite, but in practice ~30 runs × 2 small JSON files each is microseconds.
- **Import `_parse_duration` from `ls.py`, but duplicate `_data_root` and `_log_self`.** The grammar parser is pure (no I/O, no module-state); reuse is clean. The data-root and logger duplications follow the established three-module precedent. Don't pre-factor a shared `_paths.py` (prompt says "Use judgment" — judgment is "wait for 5 callers, not 3-and-a-half").
- **Compute everything once, format twice.** `_compute_run_cost` returns a structured dict that both `_format_single_plain` and `_format_single_json` consume. Don't re-traverse the data per format. Same for `--all`: compute the list of records once, then format.
- **Use `dict[str, Any]` for the computed record, but document its shape in a comment.** A `TypedDict` would be more correct but adds boilerplate that doesn't pay off for an internal type. If `view` (item #6) ends up needing it, promote to a TypedDict then.
- **`_resolve_pricing` returns (table, source_tag) once per invocation.** Don't re-resolve per run. The source tag is the same for every row of `--all` output. (Exception: rows whose model isn't in the resolved table get `pricing_source: "missing"` regardless of how the table was loaded — that tag is per-row, computed inside `_compute_run_cost`.)
- **Validate `--pricing PATH` before walking runs.** Fail fast: if the file doesn't exist or has invalid JSON, exit 2 with rc=2 before any data is read. This minimizes wasted work and gives the user a clear error.
- **`-h` text matters.** Especially for `--no-cache-cost`. Document precisely: `"exclude cache_creation cost (NOT cache_read) from the total; useful for debugging"`. The README docs phase will pick this up; the in-line help is the closest thing the v0.1 user has to documentation.
- **Plain-text output is the default; JSON is opt-in.** Don't auto-switch based on TTY for `cost` (unlike `ls`, which switches to `rich` on TTY). The prompt's plain-table example is the desired default UX. JSON consumers explicitly ask for it via `--json`.
- **Don't add `rich` to `cost.py` in v0.1.** Keeps the module simpler, keeps tests cleaner, and the plain table is already well-formed. A v0.2 polish PR can introduce `rich` rendering with TTY-gating mirrored from `ls`. The cost surface is fixed-width tables — `rich.box.SIMPLE` would look prettier but is not a v0.1 requirement.

### Test plan summary (for AC coverage)

| Acceptance criterion | Test |
|---|---|
| `agentlog cost <id>` real run prints correct table format | `test_single_run_plain_output` |
| `agentlog cost --all` prints all runs with grand total | `test_all_rollup_plain_output` |
| `agentlog cost --all` default sorts by cost desc | `test_all_default_sort_cost_desc` |
| `agentlog cost --all --source sdk` filters | `test_all_filter_source_sdk` |
| `agentlog cost --all --source hooks` filters | `test_all_filter_source_hooks` |
| `agentlog cost --all --since 1h` filters by recency | `test_all_filter_since` |
| `agentlog cost <id> --json` returns documented shape | `test_single_run_json_round_trips` |
| `agentlog cost --all --json` returns documented shape | `test_all_json_round_trips` |
| `agentlog cost <unknown-id>` exits 2 with clean error | `test_missing_run_id_exits_two` |
| Run with model not in pricing table prints `??` + exits 0 | `test_unknown_model_path_exits_zero_with_marker` |
| Run with `state.json::model = null` | `test_null_model_path` |
| Run with missing `cost.json` → zeros + rc=0 | `test_missing_cost_json_yields_zeros` |
| `--pricing /tmp/custom.json` overrides | `test_pricing_flag_overrides_model` |
| Pricing file merges with builtin (user has only one model; others inherit) | `test_pricing_merge_semantics` |
| Pricing file with custom model id | `test_pricing_custom_model` |
| `--pricing /nonexistent.json` → rc=2 | `test_pricing_missing_path_exits_two` |
| Pricing file with invalid JSON → rc=2 | `test_pricing_invalid_json_exits_two` |
| Pricing entry missing a kind → defaults to 0.0 + log | `test_pricing_missing_kind_uses_zero` |
| Pricing with negative price → defaults to 0.0 + log | `test_pricing_negative_uses_zero` |
| `$AGENTLOG_PRICING` env var | `test_pricing_env_var` |
| `$AGENTLOG_HOME/pricing.json` auto-discovery | `test_pricing_home_file_autodiscovery` |
| `pricing_source` reported in JSON (`builtin`/`file:<p>`/`missing`) | `test_pricing_source_tag_in_json` |
| `<run_id>` and `--all` mutually exclusive | `test_run_id_and_all_mutual_exclusion_exits_two` |
| Neither `<run_id>` nor `--all` → rc=2 | `test_neither_run_id_nor_all_exits_two` |
| `--since` without `--all` → rc=2 | `test_since_without_all_exits_two` |
| `--source` without `--all` → rc=2 | `test_source_without_all_exits_two` |
| `--no-cache-cost` excludes cache_creation only | `test_no_cache_cost_excludes_creation_keeps_read` |
| All-zero-usage run → `$0.0000` + rc=0 | `test_zero_usage_zero_cost` |
| Footer note on `--all` output | `test_all_output_includes_footer` |
| Footer note when unknown model in single-run | `test_unknown_model_output_includes_footer` |
| Footer suppressed in `--json` | `test_json_output_has_no_footer` |
| Existing 156 tests still pass + ruff + mypy strict clean | CI step; no specific new test |
| No new runtime dependencies | `pyproject.toml` diff check (manual or commit-message convention) |
