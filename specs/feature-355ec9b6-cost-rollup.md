# Feature: `agentlog cost` — per-run + cross-run token-to-dollar rollup

## Metadata

adw_id: `355ec9b6`
prompt: `/tmp/agentlog_step5_prompt.md` — Implement v0.1 ship-scope item #5 from DESIGN.md: `agentlog cost <id>` (per-run breakdown) plus `agentlog cost --all` (cross-run rollup with grand total). Reads `runs/<id>/cost.json` and `state.json`, multiplies by a built-in (overridable) per-model pricing table, prints a per-token-kind breakdown. Adds module `src/agentlog/cost.py`, pulls `"cost"` out of `_STUB_SUBCOMMANDS` in `cli.py`, no new runtime deps.

## Feature Description

`agentlog cost` is the dollar-amount payoff that turns the "$6,000 Claude bill" pain from `DESIGN.md` into a number the user can act on. Items #1–#4 (`init`, `uninstall`, `tail`, `ls`) are landed; runs already record token counts to `~/.agentlog/runs/<id>/cost.json` and the model id to `state.json`. This feature adds a read-only CLI surface that:

1. Resolves a per-model token pricing table from (in priority order) `--pricing PATH`, `$AGENTLOG_PRICING`, `$AGENTLOG_HOME/pricing.json`, and a built-in fallback dated 2026-05-27. User files are merged onto the built-in (user wins on collision).
2. For a single run id: prints a 3-column table (Tokens, Rate, Cost) per token kind (input / output / cache_read / cache_creation), with a Total row.
3. For `--all`: walks the runs tree (without touching the SQLite index), prints one row per run sorted by cost desc (with `started_at` desc tiebreaker), and a grand-total row plus a `(N runs)` summary line.
4. Supports `--source hooks|sdk|all`, `--since <duration>`, `--json` (machine-readable, snake_case), `--no-cache-cost` (exclude cache_creation specifically from the total; cache_read still counts), and `--pricing <path>` (override the built-in).
5. Handles unknown models gracefully (`??` in plain output, `cost_usd: null` + `pricing_source: "missing"` in JSON, rc=0 — not an error).
6. Honors all CLAUDE.md hard rules: read-only with respect to `runs/`, fail-open, stdlib-only, local-first (no network calls), schema-versioned (tolerates `cost.json::schema_version` mismatch by logging + degrading, never crashing).

## User Story

As a developer using AI coding agents (and the operator of an `agentlog`-instrumented workflow)
I want to ask "what did that run cost?" or "what did I spend today, sorted by the most expensive?"
So that I can connect each Claude Code session to a real dollar number without waiting for a monthly bill, and so I can spot expensive runs early instead of after a surprise.

## Problem Statement

`agentlog ls` already shows every captured run and TOTAL token counts, but tokens aren't actionable — the user has to mentally multiply by a per-model price they don't remember. Without a dollar view, the project's stated pain ("you wake up to $6,000 Claude bills") stays invisible at the per-run level. There is no other v0.1 surface that turns the recorded token usage into money, and no way to roll up cost across all runs in a time window.

## Solution Statement

Add a new stdlib-only module `src/agentlog/cost.py` and wire its subcommand into `src/agentlog/cli.py`:

- A hardcoded `BUILTIN_PRICING_PER_MILLION` dict for the five model ids `agentlog` will see today (`claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-sonnet-4-5`, `claude-haiku-4-5`), with an inline comment pinning the as-of date.
- A 4-level pricing-resolution layer (flag → env → `$AGENTLOG_HOME/pricing.json` → builtin) that merges user files onto the builtin so users can override only the models they care about.
- A `_compute_run_cost(run_dir, pricing, no_cache_cost)` helper that reads `state.json` + `cost.json` and returns a structured dict consumed by both the plain-text and JSON formatters.
- Plain-text formatters that produce the per-run table and the `--all` rollup with a static footer reminding users of pricing-snapshot staleness.
- JSON formatters that emit a documented, stable snake_case schema for both single-run and `--all` views.
- Fail-loud user errors (rc=2) for: unknown run id, `--pricing PATH` that doesn't exist or has invalid JSON, mutual-exclusion violation between `run_id` and `--all`, and `--since`/`--source` used without `--all`.
- A new `tests/test_cost.py` covering all acceptance criteria and edge cases.

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` — v0.1 ship scope (item #5), open question #3 (pricing source), v0.2+ deferrals. Source of truth.
- `CLAUDE.md` — hard rules #4 (no cost-budget kill-switch), #6 (local-first), #7 (schema versioning), and budget rule for read-side commands (read-only with respect to runs/).
- `ai_docs/research/355ec9b6-cost-rollup-analysis.md` — pre-planning research for this feature; component map, schema fields consumed, risks, recommendations, full AC-to-test mapping.
- `src/agentlog/cli.py` — current entry point. `SUBCOMMANDS` tuple, `_STUB_SUBCOMMANDS` frozenset, and the `for name in SUBCOMMANDS:` build loop need updates.
- `src/agentlog/_constants.py` — add `PRICING_FILE_NAME = "pricing.json"`. Reuse `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SELF_LOG_NAME`, `SOURCE_HOOKS`, `SOURCE_SDK`.
- `src/agentlog/ls.py` — reference for the read-side `_data_root` / `_log_self` / `_parse_duration` / `_format_duration` / `_started_display` helper shape. Import `_parse_duration` (pure grammar parser; no module state); duplicate `_data_root` and `_log_self` to keep `cost` independent of `ls`.
- `src/agentlog/capture.py` — source of truth for hooks-mode `state.json` and `cost.json` write paths (`schema_version: 1`, `totals: {input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}`). Read-only reference; do not modify.
- `src/agentlog/tail.py` — source of truth for SDK-mode `state.json`/`cost.json` writes. Same shape as hooks-mode. Read-only reference; do not modify.
- `tests/test_ls.py` — reference for `_seed_sdk_run` / `_seed_hooks_run` helpers; same pattern reused for `tests/test_cost.py`.
- `tests/test_cli_smoke.py` — extend the "not yet implemented" exclusion set from `{"init", "uninstall", "tail", "ls"}` to `{"init", "uninstall", "tail", "ls", "cost"}`.
- `tests/fixtures/sdk_minimal.jsonl` — SDK fixture used to seed runs in tests via `tail.run_tail`.
- `pyproject.toml` — `dependencies = []` MUST stay empty; ruff + mypy strict config already covers `src/agentlog/cost.py` and `tests/test_cost.py` once they exist.

### New Files

- `src/agentlog/cost.py` — main module (~400-500 lines). Public surface: `run_cost(*, run_id, all_, source, since, pricing_path, as_json, no_cache_cost) -> int`. Module constants: `BUILTIN_PRICING_PER_MILLION`. Internals: `_data_root`, `_log_self` (duplicated), `_load_pricing_file`, `_merge_pricing`, `_resolve_pricing`, `_compute_run_cost`, `_format_single_plain`, `_format_all_plain`, `_format_single_json`, `_format_all_json`.
- `tests/test_cost.py` — ~25-35 test functions, one per AC and edge case.

## Implementation Plan

### Phase 1: Foundation

Add the small constant for the pricing-file name, build the `cost.py` skeleton (module docstring with pinned invariants, duplicated `_data_root`/`_log_self` helpers, `from __future__ import annotations`, full type signatures, no logic yet), and define the `BUILTIN_PRICING_PER_MILLION` constant verbatim from the prompt. Verify `py_compile` and a trivial import smoke test pass before wiring into the CLI.

### Phase 2: Core Implementation

Implement the pricing-resolution stack (`_load_pricing_file`, `_merge_pricing`, `_resolve_pricing`), the per-run computation (`_compute_run_cost`), and the four formatters (plain single, plain `--all`, JSON single, JSON `--all`). Each formatter consumes the structured dict returned by `_compute_run_cost` so the data is walked once per invocation. Implement `run_cost(...)` orchestrator including mutual-exclusion checks and `--since`/`--source` filter validation. Unknown-model and missing-`cost.json` paths return non-error rc=0 with the documented degraded output.

### Phase 3: Integration

Update `src/agentlog/cli.py`: remove `"cost"` from `_STUB_SUBCOMMANDS`, add the `elif name == "cost":` branch mirroring the `ls` shape (positional `run_id` with `nargs="?"`, `--all`, `--source`, `--since`, `--pricing`, `--json`, `--no-cache-cost`), add the `_run_cost(args)` shim. Extend `tests/test_cli_smoke.py`'s exclusion set. Write `tests/test_cost.py` covering every acceptance criterion and edge case from the prompt.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Add the `PRICING_FILE_NAME` constant

- Open `src/agentlog/_constants.py`.
- Add `PRICING_FILE_NAME: str = "pricing.json"` directly after `INDEX_FILE_NAME` to keep the data-root family grouped.
- Run `py_compile` on the file to verify no syntax errors.

### 2. Create `src/agentlog/cost.py` skeleton

- Start with `"""..."""` module docstring pinning the invariants: (a) read-only w/r/t `runs/`, (b) read-only w/r/t the SQLite index from item #4 — `cost` is independent of `ls`, (c) fail-loud user CLI (rc=2 for user error), (d) stdlib-only, (e) local-first (no network), (f) per-phase math deferred to v0.2+, (g) built-in pricing dated 2026-05-27 — user is responsible for accuracy.
- `from __future__ import annotations` at top.
- Imports: stdlib only — `argparse, json, os, sys, contextlib, datetime, pathlib, typing`. From `agentlog._constants` import the five data-root names (`DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SELF_LOG_NAME`, `SOURCE_HOOKS`, `SOURCE_SDK`) plus the new `PRICING_FILE_NAME`. From `agentlog.ls` import `_parse_duration` (and optionally `_format_duration`, `_started_display` — if used, document the import).
- Duplicate `_data_root()` and `_log_self(root, message)` verbatim from `ls.py` — the established convention for the three read-side modules.
- Stub `run_cost(...)` returning 0; stub `_compute_run_cost(...)` returning an empty dict. Type every signature.

### 3. Define `BUILTIN_PRICING_PER_MILLION`

- Inline comment: `# $ per million tokens. Source: anthropic.com/pricing as of 2026-05-27.` plus the override hint `# Override with --pricing <path>, $AGENTLOG_PRICING, or $AGENTLOG_HOME/pricing.json.`
- Populate the five model ids from the prompt with the exact `input/output/cache_read/cache_creation` per-million values.
- Type: `dict[str, dict[str, float]]`.

### 4. Implement the pricing-resolution layer

- `_load_pricing_file(path: Path, root: Path) -> dict[str, dict[str, float]]`:
  - If JSON parse fails → raise a tagged exception caught by `run_cost` (rc=2 with `invalid JSON in pricing file <path>`).
  - For each model entry: if a kind is missing or non-numeric or negative, substitute `0.0` and append a `_self.log` line. Unknown extra keys are silently ignored.
  - Return the (possibly partial) parsed table.
- `_merge_pricing(builtin: dict, user: dict) -> dict`: deep-merge; user keys override builtin keys at the model level (user wins). A user entry replaces the entire model row (not per-kind merge, per prompt) — but a model present in builtin and absent from user is preserved.
- `_resolve_pricing(pricing_flag: Path | None, root: Path) -> tuple[dict, str]`:
  - Resolution order: `--pricing PATH` → `$AGENTLOG_PRICING` → `$AGENTLOG_HOME/pricing.json` → `BUILTIN_PRICING_PER_MILLION`.
  - First three are merge sources; the fourth is the fallback when no user file is found.
  - If `--pricing PATH` is passed and the path doesn't exist → tagged exception → rc=2 with `pricing file not found: <path>`.
  - Env-var and home-file paths that don't exist are silently treated as absent (not an error — they're discovery, not assertion).
  - Returns `(merged_table, source_tag)` where `source_tag` is `"builtin"` if no user file was found, or `"file:<absolute-path>"` if one was.

### 5. Implement `_compute_run_cost`

- Signature: `_compute_run_cost(run_dir: Path, pricing: dict, pricing_source: str, no_cache_cost: bool) -> dict[str, Any]`.
- Read `state.json`: extract `session_id`, `source`, `model`, `started_at`, `ended_at`. Tolerate missing fields by defaulting to `None`. Tolerate schema_version mismatch by logging + continuing.
- Read `cost.json::totals` (default zeros if file missing or `totals` empty).
- Look up `model` in the pricing table:
  - Hit: compute `cost = tokens * rate_per_million / 1_000_000.0` for each of the four kinds. If `no_cache_cost` is True, zero out the `cache_creation` cost contribution (but keep the cache_creation token count in the displayed/JSON output for transparency). `cache_read` is unaffected.
  - Miss (model None, empty, or absent from pricing): set `cost_usd = None`, `rates_per_million_usd = None`, mark `pricing_source = "missing"`, set `cost_unknown_reason = "model not in pricing table"`.
- Returns the structured dict consumed by both formatters. Document the shape in a comment (no TypedDict in v0.1).

### 6. Implement plain-text formatters

- `_format_single_plain(record) -> str`:
  - Header block: `Run:`, `Source:`, `Model:`, `Started:` (use `_started_display` from `ls.py` semantics), `Duration:` (use `_format_duration` semantics; `-` if `ended_at` is `None`).
  - 3-column table with right-aligned numeric columns: `Tokens`, `Rate`, `Cost`. Four rows (Input / Output / Cache read / Cache create), separator `---`, `Total` row.
  - Money format: `f"${value:.4f}"` (4 dp, no locale).
  - Token format: `f"{n:,}"`.
  - Rate format: `f"${rate:.2f} / 1M tokens"`.
  - Unknown-model: render `??` for rate and cost cells. After the table, print a single-line note: `note: model '<X>' not in pricing table; cost cannot be computed. Set $AGENTLOG_PRICING or pass --pricing.`
  - If `pricing_source` is `"builtin"` and model is known, append the staleness footer.
- `_format_all_plain(records, summary, source_tag) -> str`:
  - 4-column table: `RUN ID`, `MODEL`, `TOKENS`, `COST`. Right-aligned numeric columns.
  - Default sort: cost desc with `started_at` desc tiebreaker. Unknown-cost rows sort last (treat `cost_usd is None` as `-inf` for the sort key).
  - Trailing separator + `TOTAL  (N runs)` row showing summed tokens and summed cost (excluding unknown-cost rows from the cost sum; include their token counts in the token sum). Mention `unknown cost: N runs` in the summary line if any.
  - Footer always shown: `pricing snapshot: built-in (2026-05-27). Override with --pricing <file> or $AGENTLOG_PRICING.` — unless `pricing_source` is a file path, in which case show: `pricing source: <path>`.
  - If zero runs match: print `no runs match the filter` (`--source`/`--since` filters) or `no runs found at <path>` (empty tree). Both rc=0.

### 7. Implement JSON formatters

- `_format_single_json(record) -> str`: snake_case keys matching the schema in the prompt. Pass `started_at` and `ended_at` through verbatim (no reformatting — machines consume the microsecond form). Include `duration_seconds` as an integer (or `null` if `ended_at` is `None`). Include `pricing_source`. On unknown-model: omit `cost_usd` and `rates_per_million_usd`, include `"pricing_source": "missing"` and `"cost_unknown_reason": "model not in pricing table"`.
- `_format_all_json(records, summary) -> str`: `{"runs": [...], "summary": {...}}` shape. Summary keys: `run_count`, `total_tokens`, `total_cost_usd`. If any unknown-cost runs were present: also `"unknown_cost_runs": N`. Footer is NOT included in JSON output.
- Both use `json.dumps(payload, indent=2)` (single-shot, no streaming).

### 8. Implement `run_cost(...)` orchestrator

- Resolve data root via `_data_root()`.
- Validate flag combinations BEFORE any I/O:
  - `run_id` and `--all` both given → rc=2 with `'<run_id>' and --all are mutually exclusive`.
  - Neither given → rc=2 with usage message.
  - `--source` or `--since` given without `--all` → rc=2 with explanatory message.
- Resolve pricing via `_resolve_pricing(pricing_flag, root)`. Convert any tagged exception to rc=2 with the documented message.
- Single-run path:
  - If `runs/<run_id>/state.json` missing → rc=2 with `run id '<X>' not found at <path>`.
  - Else: compute one record, format (JSON or plain), print, rc=0.
- `--all` path:
  - Walk `runs/`. Skip entries that are not directories or lack `state.json`.
  - Apply `--source` filter (compare against `state.json::source`).
  - Apply `--since` filter (compare `state.json::started_at` against `now - duration`, using `_parse_duration`).
  - Compute records, sort (cost desc, started_at desc tiebreaker, unknown last), format with grand total, print, rc=0.
- Returns int rc. Wrap top-level unexpected I/O failures in rc=1 with stderr message; keep the catch narrow (don't swallow `KeyboardInterrupt` or `SystemExit`).

### 9. Wire `cost` into the CLI

- Edit `src/agentlog/cli.py`:
  - Update the top import: `from agentlog import __version__, capture, cost, hooks_install, ls, tail`.
  - Remove `"cost"` from `_STUB_SUBCOMMANDS` (leaves `{"view"}`).
  - Insert an `elif name == "cost":` branch mirroring the `ls` shape (after the `ls` branch, before the stub fallthrough):
    - Positional `run_id` with `nargs="?"`.
    - `--all` (store_true).
    - `--source` (choices: `hooks`, `sdk`, `all`; default `all`).
    - `--since` (string, accepted by `ls._parse_duration`).
    - `--pricing` (Path, default None).
    - `--json` (store_true).
    - `--no-cache-cost` (store_true; help text: `exclude cache_creation cost (NOT cache_read) from the total; useful for debugging`).
    - `sp.set_defaults(func=_run_cost)`.
  - Add `_run_cost(args)` thin shim below `_run_ls(args)` that calls `cost.run_cost(...)` with named arguments.

### 10. Update `tests/test_cli_smoke.py`

- Locate the "not yet implemented" parametrised test (the exclusion set `{"init", "uninstall", "tail", "ls"}`).
- Extend to `{"init", "uninstall", "tail", "ls", "cost"}`.

### 11. Create `tests/test_cost.py`

- Mirror the `test_ls.py` shape:
  - `from __future__ import annotations` at top.
  - `import json, os` and the public symbols under test from `agentlog import cost`.
  - Helper `_seed_sdk_run(tmp_path, run_id, ...)` invoking `tail.run_tail` against `tests/fixtures/sdk_minimal.jsonl`.
  - Helper `_seed_hooks_run(tmp_path, session_id, ...)` invoking `capture.dispatch`.
  - Helper `_seed_unknown_model_run(tmp_path, run_id, model)` writing a hand-rolled `state.json` + `cost.json` directly.
  - Helper `_seed_run_without_cost_json(tmp_path, run_id)` writing only `state.json`.
- Each test function monkeypatches `AGENTLOG_HOME` to `tmp_path` and exercises the public surface (`cost.run_cost(...)` or via `cli.main([...])`). Capture stdout/stderr with `capsys`.
- Implement every test from the table in `ai_docs/research/355ec9b6-cost-rollup-analysis.md` section "Test plan summary (for AC coverage)" (one test per acceptance criterion plus the edge-case rows).

### 12. Compile + import smoke check

- `.venv/bin/python -m py_compile src/agentlog/cost.py src/agentlog/cli.py src/agentlog/_constants.py`.
- `.venv/bin/python -c "from agentlog import cost; print(cost.run_cost.__name__)"`.
- `.venv/bin/agentlog cost --help`.
- `.venv/bin/agentlog cost --all` against the operator's real `~/.agentlog/runs/` (sanity check, not a test).

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation:

- No `HOW_TO_CREATE_TESTS.md` or `TESTING.md` exists in `tests/`; conventions are encoded in the existing files (`test_capture.py`, `test_tail.py`, `test_ls.py`, `test_hooks_install.py`, `test_cli_smoke.py`, `test_handler_perf.py`). Follow `test_ls.py` most closely — it's the nearest neighbor (also a read-side, stdlib-only CLI module).
- Tests use `pytest` + `tmp_path` + `monkeypatch` + `capsys`. No external libraries. Seed runs through production functions (`tail.run_tail`, `capture.dispatch`) — never bypass the writer to forge raw files unless the test specifically targets a hand-rolled edge case (e.g., unknown-model or missing-`cost.json`).
- Monkeypatch `AGENTLOG_HOME` to `tmp_path` in every test that touches the runs tree.
- Never hardcode model names outside the pricing table itself; pull from `state.json` after seeding so the test follows the same data path as production.

### Unit Tests

- **Pricing resolution & merge**:
  - `test_pricing_flag_overrides_model` — `--pricing` flag wins over builtin.
  - `test_pricing_merge_semantics` — user file with one model overrides that row; the four other builtin models stay reachable.
  - `test_pricing_custom_model` — user-supplied model id not in builtin still resolves.
  - `test_pricing_env_var` — `$AGENTLOG_PRICING` is consulted when `--pricing` not given.
  - `test_pricing_home_file_autodiscovery` — `$AGENTLOG_HOME/pricing.json` auto-loaded.
  - `test_pricing_missing_path_exits_two` — `--pricing /nonexistent.json` → rc=2 BEFORE any computation.
  - `test_pricing_invalid_json_exits_two` — invalid JSON → rc=2 with `invalid JSON in pricing file <path>`.
  - `test_pricing_missing_kind_uses_zero` — entry with missing kind: kind defaults to 0.0, `_self.log` line appended.
  - `test_pricing_negative_uses_zero` — negative price: defaults to 0.0, `_self.log` line appended.
- **Computation**:
  - `test_single_run_plain_output` — real run matches the documented table format.
  - `test_single_run_json_round_trips` — JSON output parses and matches the documented schema.
  - `test_zero_usage_zero_cost` — all-zero tokens → `$0.0000`, rc=0.
  - `test_no_cache_cost_excludes_creation_keeps_read` — `--no-cache-cost` zeros out cache_creation contribution but cache_read still in total.
- **CLI surface**:
  - `test_run_id_and_all_mutual_exclusion_exits_two`.
  - `test_neither_run_id_nor_all_exits_two`.
  - `test_since_without_all_exits_two`.
  - `test_source_without_all_exits_two`.
  - `test_cli_smoke` already verifies `cost --help` runs.

### Integration Tests

- **End-to-end seeded runs**:
  - `test_all_rollup_plain_output` — seed 3 SDK runs of differing cost; verify total + per-row math.
  - `test_all_default_sort_cost_desc` — verify default sort with a deterministic tiebreaker (`started_at` desc).
  - `test_all_filter_source_sdk` — seed both hooks + SDK runs; `--source sdk` filters correctly.
  - `test_all_filter_source_hooks` — same, opposite filter.
  - `test_all_filter_since` — seed runs at different `started_at` values; `--since 1h` filters by recency.
  - `test_all_json_round_trips` — JSON shape for the rollup.
  - `test_all_output_includes_footer` — footer always appears in plain `--all`.
  - `test_all_no_runs_match_filter` — filter that excludes all runs → rc=0 with `no runs match the filter`.
  - `test_all_empty_tree` — no runs at all → rc=0 with `no runs found at <path>`.
- **Edge cases**:
  - `test_missing_run_id_exits_two` — `agentlog cost <unknown-id>` → rc=2 with documented message.
  - `test_missing_cost_json_yields_zeros` — run dir without `cost.json` (e.g., crashed before Stop fired) → zeros, rc=0.
  - `test_unknown_model_path_exits_zero_with_marker` — model not in pricing table → `??` rendering, rc=0.
  - `test_null_model_path` — `state.json::model = null` → unknown-model path.
  - `test_unknown_model_output_includes_footer` — unknown-model single-run output prints the corrective note.
  - `test_json_output_has_no_footer` — `--json` mode never prints the staleness footer.
  - `test_pricing_source_tag_in_json` — `pricing_source` shows `builtin` / `file:<abs>` / `missing` correctly per row.

### Edge Cases

- `cost.json::schema_version` mismatch — log to `_self.log`, continue with whatever fields are present (do not crash).
- `state.json::ended_at = null` (in-flight session) — `Duration: -` in plain output, `duration_seconds: null` in JSON; rc=0.
- Run dirs that aren't directories (stray files in `runs/`) — silently skip.
- Run dirs missing `state.json` — silently skip (mid-write or crashed run).
- `--source` value not in the `{hooks, sdk, all}` choice set — argparse rejects with rc=2 (standard argparse behavior; no custom handling needed).
- Pricing-table model id with whitespace/case differences (`Claude-Opus-4-7 ` vs `claude-opus-4-7`) — exact-match only in v0.1; document in the help text. A typo in `state.json::model` falls through to the unknown-model path.
- Concurrent invocations of `cost` — fully read-only, no locking concerns; `_self.log` appends are short and POSIX-atomic.
- Float-precision: results display to 4 decimals via `f"${value:.4f}"`. No `Decimal` in v0.1.
- `--all` against ~30 runs — Python `Path.iterdir()` + JSON parses are microseconds; no perf budget required, but assert the call returns in well under one second in a smoke test (optional).

## Acceptance Criteria

- `agentlog cost <id>` for a real run id from `agentlog ls` prints the documented table format with correct math, header (Run / Source / Model / Started / Duration), per-kind rows (Input / Output / Cache read / Cache create), Total row, and rc=0.
- `agentlog cost --all` prints all runs in `~/.agentlog/runs/` with a grand total, default-sorted by cost desc (with `started_at` desc tiebreaker), and always shows the pricing-snapshot footer.
- `agentlog cost --all --source sdk` filters to SDK runs; `--source hooks` filters to hooks runs.
- `agentlog cost --all --since 1h` filters to runs whose `started_at` is within the last hour (using `ls._parse_duration` grammar).
- `agentlog cost <id> --json` returns the documented snake_case JSON shape; `agentlog cost --all --json` returns `{"runs": [...], "summary": {...}}`.
- `agentlog cost <unknown-id>` exits 2 with `run id '<X>' not found at <path>` on stderr.
- A run whose model is not in the resolved pricing table prints `??` in rate + cost columns (plain) or omits `cost_usd`/`rates_per_million_usd` and sets `"pricing_source": "missing"` (JSON); rc=0.
- `--pricing /tmp/custom.json` where the file overrides one model and inherits the rest from the builtin works correctly.
- `--pricing /nonexistent.json` → rc=2 with `pricing file not found: <path>` BEFORE any computation.
- `--pricing PATH` to a file with invalid JSON → rc=2 with `invalid JSON in pricing file <path>`.
- `$AGENTLOG_PRICING` and `$AGENTLOG_HOME/pricing.json` are honored in priority order.
- Pricing source is reported in JSON output as one of `builtin`, `file:<absolute-path>`, or `missing`.
- `<run_id>` and `--all` are mutually exclusive (rc=2). Neither given → rc=2 with usage message. `--since`/`--source` without `--all` → rc=2.
- `--no-cache-cost` excludes cache_creation cost contribution from the total; cache_read cost is preserved.
- Existing 156 tests still pass; new `tests/test_cost.py` covers every AC and edge case.
- `pyproject.toml dependencies = []` is unchanged.
- ruff (with the existing `E/W/F/I/B/UP/SIM` ruleset) and mypy strict on `src` and `tests` remain clean.
- README is NOT touched (docs phase generates `docs/feature-355ec9b6-cost-rollup.md` separately).
- All CLAUDE.md hard rules respected: read-only re: `runs/`, fail-open (no crashes from malformed pricing files or schema_version mismatch), stdlib-only, local-first (zero network calls), schema-versioned tolerance.

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These run during the build phase — do NOT include pytest, linters, or pipeline runs (those belong to dedicated CI phases).

- `.venv/bin/python -m py_compile src/agentlog/cost.py && echo "OK"` — verify no syntax errors in the new module.
- `.venv/bin/python -m py_compile src/agentlog/cli.py && echo "OK"` — verify the CLI wiring compiles.
- `.venv/bin/python -m py_compile src/agentlog/_constants.py && echo "OK"` — verify the constant addition compiles.
- `.venv/bin/python -c "from agentlog import cost; print('import OK')"` — verify the module imports cleanly with no side effects.
- `.venv/bin/python -c "from agentlog import cost; assert 'claude-opus-4-7' in cost.BUILTIN_PRICING_PER_MILLION; print('pricing OK')"` — verify the built-in pricing table loaded.
- `.venv/bin/agentlog --help | grep cost` — verify the subcommand is registered.
- `.venv/bin/agentlog cost --help` — verify the subparser builds.

## Notes

- **No new runtime dependencies.** Stdlib-only. `pyproject.toml dependencies = []` stays empty.
- **No README changes** in this phase. The docs phase will write `docs/feature-355ec9b6-cost-rollup.md` separately. The in-line `--help` text is the v0.1 user-facing documentation.
- **Pricing-table staleness disclosure.** The built-in is dated 2026-05-27. The footer in plain output and the inline module comment make this explicit. The docs phase will further document override mechanics. Do NOT bury the disclosure.
- **Float-precision is fine for v0.1.** IEEE-754 doubles give ~15.95 decimal digits of precision; we display 4. If a future user reports a 1-cent discrepancy on a billion-token run, revisit with `Decimal`. Not v0.1.
- **`view` (item #6) reuse.** Keep `_compute_run_cost` reachable but module-private for v0.1. When item #6 lands, it can absorb the "promote to public API" decision (`view <id>` will likely embed the cost block in its TUI).
- **Privacy.** Local-first. No network calls. No telemetry. No PII flows through `cost`; it consumes locally-recorded token counts and a pricing table. Same posture as `ls`.
- **Future deferrals** (NOT in v0.1): per-phase breakdown within a single run, cost-over-time charts, cost-by-cwd / cost-by-model aggregation, pricing-table auto-update from anthropic.com, cost-budget kill-switch (`--max-spend`). These map to existing DESIGN.md v0.2+ items and the prompt's "Out of scope" section.
- **Why duplicate `_data_root` and `_log_self`?** The three read-side modules (`capture`, `tail`, `ls`) already duplicate these. The convention is "wait for 5 callers before factoring a shared helper, not 3-and-a-half." `_parse_duration` is the exception: pure grammar parser, no module state — importing it from `ls` is single-source-of-truth.
- **Why no `rich`?** The prompt's output examples are plain-text fixed-width tables. Keeping `cost.py` `rich`-free simplifies tests and matches v0.1's minimalism. A v0.2 polish PR can add `rich` rendering with TTY-gating mirrored from `ls`.
