# `agentlog cost` — Per-Run and Cross-Run Token-to-Dollar Rollup

**ADW ID:** 355ec9b6
**Date:** 2026-05-27
**Specification:** specs/feature-355ec9b6-cost-rollup.md

## Overview

Implements `agentlog cost`, the dollar-amount view that turns recorded token counts into actionable spend numbers. A single run id prints a 4-row (input / output / cache read / cache create) breakdown with per-kind rates and costs; `--all` walks the entire runs tree and emits a grand-total rollup sorted by cost descending. Pricing resolves from a 4-level stack (CLI flag → env var → `$AGENTLOG_HOME/pricing.json` → built-in table dated 2026-05-27) with user files merged onto the built-in so partial overrides work.

## What Was Built

- `src/agentlog/cost.py` — new stdlib-only module (~780 lines); public surface: `run_cost(...) -> int`
- `BUILTIN_PRICING_PER_MILLION` — hardcoded per-million pricing for five Claude model IDs as of 2026-05-27
- 4-level pricing-resolution stack with merge semantics (user wins per model row, builtin fills the rest)
- `_compute_run_cost` — reads `state.json` + `cost.json`, returns structured dict consumed by all four formatters
- Plain-text formatters: aligned 3-column table (Tokens / Rate / Cost) for single-run; 4-column rollup with total row for `--all`
- JSON formatters: stable snake_case schema for both single-run and `--all` views
- `PRICING_FILE_NAME = "pricing.json"` constant added to `src/agentlog/_constants.py`
- `cost` subparser wired into `src/agentlog/cli.py`; removed from `_STUB_SUBCOMMANDS`
- `tests/test_cost.py` — 30+ test functions covering all acceptance criteria and edge cases

## Technical Implementation

### Files Modified

- `src/agentlog/cost.py`: new module; public surface `run_cost(...) -> int`, module constant `BUILTIN_PRICING_PER_MILLION`
- `src/agentlog/_constants.py`: added `PRICING_FILE_NAME = "pricing.json"` after `INDEX_FILE_NAME`
- `src/agentlog/cli.py`: removed `"cost"` from `_STUB_SUBCOMMANDS`; added `cost` subparser with all flags; added `_run_cost` shim; added `from pathlib import Path` import; broadened top import to include `capture`, `cost`, `ls`, `tail`
- `tests/test_cli_smoke.py`: extended stub exclusion set to include `"cost"`
- `tests/test_cost.py`: new file

### Key Changes

- **Pricing resolution** is a 4-level stack: `--pricing PATH` (must exist, rc=2 if not) → `$AGENTLOG_PRICING` env var (silently skipped if path absent) → `$AGENTLOG_HOME/pricing.json` (silently skipped if absent) → `BUILTIN_PRICING_PER_MILLION`. The first user file found is deep-merged onto the built-in so users override only the models they care about.
- **`_compute_run_cost`** reads `state.json` and `cost.json` with safe fallbacks: missing files yield zeros; schema_version mismatches log to `_self.log` and continue rather than crashing. Unknown or null model IDs set `pricing_source = "missing"` and `cost_usd = None` with rc=0.
- **`--no-cache-cost`** zeroes out the `cache_creation` cost contribution while keeping the token count visible; `cache_read` is unaffected.
- **`--all` sort order**: cost descending, `started_at` descending as tiebreaker; runs with unknown cost sort last (treated as `-inf`).
- **`_parse_duration` imported from `ls`** (single-source-of-truth for the duration grammar); `_data_root`, `_log_self`, `_format_duration`, and `_started_display` are duplicated per the established convention for read-side modules (wait for five callers before factoring).

### Architecture Impact

Adds a new read-side module independent of the SQLite index (`cost` walks `runs/` directly via `Path.iterdir()`, never reads `index.sqlite3`). The `_compute_run_cost` helper is intentionally module-private in v0.1 to leave the promotion-to-public-API decision to the `agentlog view` implementation (item #6). No change to the write path — hooks handlers and `tail` are untouched. No new runtime dependencies; `pyproject.toml dependencies = []` remains empty.

## How to Use

### CLI Commands

```bash
# Per-run breakdown for a known run id
agentlog cost <run-id>

# Cross-run rollup (sorted by cost descending)
agentlog cost --all

# Filter by source and/or time window
agentlog cost --all --source sdk
agentlog cost --all --source hooks --since 24h

# Machine-readable JSON
agentlog cost <run-id> --json
agentlog cost --all --json

# Exclude cache_creation cost from totals (cache_read still counted)
agentlog cost <run-id> --no-cache-cost
agentlog cost --all --no-cache-cost

# Override pricing table
agentlog cost --all --pricing /path/to/custom_pricing.json
AGENTLOG_PRICING=/path/to/pricing.json agentlog cost --all
```

### Custom Pricing File

```json
{
  "claude-sonnet-4-6": {
    "input": 3.00,
    "output": 15.00,
    "cache_read": 0.30,
    "cache_creation": 3.75
  }
}
```

Models not present in the user file inherit their rates from the built-in table.

### Programmatic Usage

```python
from agentlog import cost

rc = cost.run_cost(
    run_id="abc123",
    all_=False,
    source="all",
    since=None,
    pricing_path=None,
    as_json=True,
    no_cache_cost=False,
)
```

## Configuration

| Method | Priority | Notes |
|--------|----------|-------|
| `--pricing PATH` | Highest | rc=2 if path does not exist |
| `$AGENTLOG_PRICING` | 2nd | Silently ignored if path absent |
| `$AGENTLOG_HOME/pricing.json` | 3rd | Silently ignored if file absent |
| Built-in table | Fallback | Dated 2026-05-27; staleness footer shown in plain output |

`$AGENTLOG_HOME` overrides the default data root (`~/.agentlog`).

## Testing

```bash
pytest tests/test_cost.py -v
```

The test suite uses `pytest` + `tmp_path` + `monkeypatch` + `capsys`. Runs are seeded through production functions (`tail.run_tail`, `capture.dispatch`) rather than forging raw files, except for edge-case fixtures (unknown model, missing `cost.json`). `AGENTLOG_HOME` is monkeypatched to `tmp_path` in every test.

Key coverage areas:
- Pricing resolution (flag / env var / home file / built-in / merge semantics)
- Invalid pricing inputs (missing path rc=2, invalid JSON rc=2, negative/missing kinds → 0.0)
- Single-run plain and JSON output format correctness
- `--all` rollup: sort order, totals, unknown-cost rows, footer
- Filters: `--source sdk`, `--source hooks`, `--since`
- Edge cases: unknown model (`??` / `pricing_source: "missing"`), missing `cost.json` (zeros), `null` model, in-flight session (`ended_at: null`), empty runs tree
- CLI mutual-exclusion errors (rc=2): `run_id` + `--all`, neither given, `--since`/`--source` without `--all`
- `--no-cache-cost` excludes `cache_creation` but not `cache_read`

## Notes

- **Pricing staleness disclosure.** The built-in table is dated 2026-05-27. The footer always appears in plain output (except when a user-supplied pricing file is active, in which case the file path is shown instead). JSON output never includes the footer.
- **Float precision.** Costs display to 4 decimal places via `f"${value:.4f}"`. `Decimal` not used in v0.1; revisit if sub-cent discrepancies matter at billion-token scale.
- **Privacy.** Fully local-first. No network calls. No telemetry. Only locally-recorded token counts and a local pricing table are accessed.
- **`view` reuse.** `_compute_run_cost` is module-private in v0.1. When `agentlog view <id>` (item #6) lands it can absorb the cost block with a single promotion decision.
- **Per-phase breakdown** (cost per tool-use phase within a run) is deferred to v0.2+.
- **`--pricing` exact-match only.** Model IDs in `state.json` are matched case-sensitively and without whitespace normalization. A typo in the recorded model ID falls through to the unknown-model path.
