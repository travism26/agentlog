# `agentlog view` — Three-Panel TUI Hero Artifact

**ADW ID:** cb153ac3
**Date:** 2026-05-27
**Specification:** specs/feature-cb153ac3-view-tui.md

## Overview

`agentlog view <id>` is the per-run inspection surface for v0.1: given a run id from `agentlog ls`, it reads `runs/<id>/state.json`, `events.jsonl`, and `cost.json` and renders a single static screen in three vertical sections — a header card, a chronological event timeline, and a cost footer. This is the README hero screenshot command: it makes `agentlog`'s pitch ("you can finally see what your agent actually did") into a visual artifact.

## What Was Built

- `src/agentlog/view.py` — ~630-line module with the full read path and rich-gated three-panel renderer
- `src/agentlog/cli.py` — `"view"` removed from `_STUB_SUBCOMMANDS`; `elif name == "view":` subparser branch added with all four flags; `_run_view` shim wired
- `tests/test_view.py` — ~873-line test file covering every acceptance criterion and edge case

## Technical Implementation

### Files Modified

- `src/agentlog/view.py` *(new)*: main module implementing `run_view(...)` and all rendering helpers
- `src/agentlog/cli.py`: removed `"view"` from `_STUB_SUBCOMMANDS` (now `frozenset()`); added `view` subparser and `_run_view` shim
- `tests/test_cli_smoke.py`: added `"view"` to the exclusion list so the "not yet implemented" assertion no longer fires
- `tests/test_view.py` *(new)*: 873-line test suite

### Key Changes

- **Three-panel renderer**: `_render_header_rich` (HEAVY-box `rich.Panel` with seven metadata rows), `_render_timeline_rich` (angle-bracket left rail, per-kind color styles, `--limit` cap hint), `_render_cost_footer_rich` (aligned four-row table reusing `cost._TOKEN_KIND_LABELS` / `_KIND_DISPLAY_ORDER`)
- **Per-tool dispatch table** (`_TOOL_SUMMARIZERS`): covers `Read`, `Edit`, `Write`, `Grep`, `Bash`, `Glob` with a `_default_summarizer` fallback for unknown tools. Dispatch-table key invariant is enforced in `test_view_tool_summarizers_table_keys_match_documented_set` (lesson #9)
- **`rich` import gated inside `run_view`** after the `--json` branch returns; `view --json` works without `rich` installed (lesson #8 invariant, see module docstring rule #3)
- **ANSI escape stripper** (`_strip_ansi`) applied to all event text fields before rendering to prevent terminal corruption from agent-payload escape sequences
- **Sort-by-timestamp enforced explicitly** in `_load_events` (lesson #1 regression: file order is not trusted; events sorted by `timestamp` ascending, with UTC-aware `datetime.min` fallback for malformed timestamps)
- **Graceful degradation**: missing `events.jsonl` renders `(no events recorded)` placeholder; missing `cost.json` renders `(no cost data recorded)` placeholder; only missing `state.json` is a hard rc=2 error

### Architecture Impact

`view` is a pure reader of `runs/<id>/`. It imports from `cost` (`_compute_run_cost`, `_resolve_pricing`, `_TOKEN_KIND_LABELS`, `_KIND_DISPLAY_ORDER`, `_PRICING_STALENESS_FOOTER`) and `ls` (`_format_duration`, `_started_display`). The strict DAG (`ls → cost → view`) means no circular-import risk. Helpers `_data_root()` and `_log_self()` are duplicated from `cost.py`/`tail.py` per the established v0.2+ `_io.py` deferral precedent. No hook-path code is touched; `view` is entirely in the read-time (CLI) path and has zero impact on the <10ms hook-handler latency budget.

## How to Use

### CLI Commands

```bash
# Inspect a run (rich must be installed)
agentlog view <run-id>

# Show only the event timeline
agentlog view <run-id> --events-only

# Show all events without truncation
agentlog view <run-id> --no-truncate

# Limit timeline to first N events (default 100; 0 = unlimited)
agentlog view <run-id> --limit 20

# Machine-readable JSON output (works without rich)
agentlog view <run-id> --json

# Pipe to a pager (ANSI colors preserved, no terminal corruption)
agentlog view <run-id> | less -R
```

### JSON output shape

```json
{
  "run_id": "<id>",
  "state": { ... },
  "cost": {
    "totals": { ... },
    "pricing_source": "builtin",
    "computed": { "input_tokens": 0.0, ... },
    "cost_usd": 0.0042
  },
  "events": [ ... ]
}
```

### Programmatic Usage

```python
from agentlog.view import run_view

rc = run_view(
    run_id="hooks-abc123",
    limit=100,
    events_only=False,
    no_truncate=False,
    as_json=False,
)
# rc: 0=success, 1=I/O error or missing rich, 2=user error
```

## Configuration

No new configuration. `rich` must be installed for the TUI panels; `--json` works without it.

```bash
# Install rich (already in the [tui] extra)
pip install 'agentlog[tui]'
# or
uv pip install 'agentlog[tui]'
```

`AGENTLOG_HOME` overrides the default `~/.agentlog` data root (same as all other subcommands).

## Testing

```bash
pytest tests/test_view.py -v
```

Key test coverage:

| Test | What it verifies |
|------|-----------------|
| `test_view_tool_summarizers_table_keys_match_documented_set` | Dispatch table keys exactly `{Read, Edit, Write, Grep, Bash, Glob}` (lesson #9) |
| `test_view_timeline_renders_older_event_above_newer` | `_load_events` sorts by `timestamp` ascending regardless of file order (lesson #1 regression) |
| `test_view_strip_ansi_removes_escape_sequences` | `\x1b[2J\x1b[H` and color codes are stripped before rendering |
| `test_view_json_mode_emits_combined_object_without_rich` | `--json` works with `sys.modules["rich"] = None` |
| `test_view_missing_rich_returns_rc_1_with_install_hint` | Correct error message and rc=1 when rich absent |
| `test_view_missing_state_returns_rc_2` | rc=2 + stderr `not found` for missing run id |
| `test_view_json_against_nonexistent_run_returns_rc_2_with_no_partial_output` | No partial JSON emitted on rc=2 |
| `test_view_limit_5_shows_more_hint` | `… (N more events; use --limit 0 to see all)` hint appears |
| `test_view_events_only_skips_header_and_cost` | `--events-only` suppresses header panel and cost footer |

## Notes

- **Local-first / privacy**: `view` is purely a reader of local `runs/<id>/` files. `--json` emits the same data already on disk; nothing is sent over the network.
- **Read-only contract**: `view` must not mutate `state.json`, `events.jsonl`, `cost.json`, or the SQLite index. CLAUDE.md rule #2 (fail-open) does NOT apply here — this is a user-invoked CLI that should fail-loud like `ls`/`cost`.
- **Deferred to v0.2+**: interactive `textual` TUI, `agentlog replay <id>` step-through playback, `agentlog diff <a> <b>`, per-event filtering flags (`--only tool_use`), and embedded code-diff rendering from Edit tool calls.
- **`_STUB_SUBCOMMANDS` is now `frozenset()`** — all six v0.1 subcommands are fully implemented. `tests/test_cli_smoke.py` exclusion list updated to include `"view"` (lesson #4).
