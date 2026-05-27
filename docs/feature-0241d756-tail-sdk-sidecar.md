# Feature: `agentlog tail` — SDK Sidecar Mode

**ADW ID:** `0241d756`
**Date:** 2026-05-27
**Specification:** `specs/feature-0241d756-tail-sdk-sidecar.md`

## Overview

Implements `agentlog tail <path>` — the scripted-mode twin of the hook-based capture pipeline. Where the hook handlers capture interactive `claude` sessions, `tail` ingests existing `cc_raw_output.jsonl` artifacts produced by Claude Code SDK / Anthropic SDK runs into the same unified `runs/<id>/{state.json, events.jsonl, cost.json}` schema. This proves the central design thesis: both interactive and scripted runs share one schema, discriminated only by `source: "hooks" | "sdk"`.

## What Was Built

- `src/agentlog/tail.py` — new module implementing the full SDK sidecar translator (~250 LOC)
- `src/agentlog/_constants.py` — added `SOURCE_SDK = "sdk"` constant
- `src/agentlog/cli.py` — wired real `tail` subparser with four flags; also wired `_hook` to `capture.run_hook`
- `tests/test_tail.py` — 29 tests covering happy path, idempotency, `--force`, `--dry-run`, directory walk, edge cases, and unit-level translator tests
- `tests/fixtures/sdk_minimal.jsonl` — hand-written minimal stream-json fixture (6 records)
- `tests/test_cli_smoke.py` — updated to exclude `"tail"` from the stub-subcommand check

## Technical Implementation

### Files Modified

- `src/agentlog/tail.py` *(new)*: translator, run-id derivation, per-file processor, and `run_tail()` public entry point
- `src/agentlog/_constants.py`: added `SOURCE_SDK: str = "sdk"` alongside `SOURCE_HOOKS`
- `src/agentlog/cli.py`: removed `"tail"` from `_STUB_SUBCOMMANDS`; added real `tail` subparser with `path`, `--run-id`, `--source-name`, `--dry-run`, `--force`; wired `_hook` to `capture.run_hook`
- `tests/test_tail.py` *(new)*: full test suite
- `tests/fixtures/sdk_minimal.jsonl` *(new)*: stable test fixture
- `tests/test_cli_smoke.py`: excluded `"tail"` from stub parametrize set

### Key Changes

- **Run-id derivation** (`_derive_run_id`): reads only the first non-blank line to find the `system/init` record and extract `session_id` → `sdk-<session_id>`. Falls back to `sdk-<sha1(abspath)[:12]>` for truncated or empty files, writes a warning to `_self.log`.
- **Pure translator** (`_translate`): no I/O, no clock — caller injects `now` and `abs_path` for deterministic tests. Handles `system/init` → `session_start`, `assistant` text and tool_use blocks → `assistant_text` / `tool_use`, `user` text prompts → `prompt`, `result` → `stop`, everything else → `event: "unknown"` with raw payload.
- **Fail-loud contract**: unlike `capture.run_hook` (which exits 0 always), `tail` surfaces I/O errors via stderr and non-zero exit codes (`0` = success, `1` = I/O failure, `2` = user error).
- **Idempotency**: checks for `events.jsonl` existence before writing; `--force` unlinks only `state.json`, `events.jsonl`, and `cost.json` — not the entire run directory.
- **Stream-line iteration**: never loads the full file into memory; processes one JSON line at a time, safe for large files (>10 MB).

### Architecture Impact

- Completes the unified-schema thesis from `DESIGN.md` lines 87–131: `runs/<id>/` directories now hold both hooks-mode and SDK-mode output, differentiated only by `source`.
- `assistant_text` is a new event kind introduced by this module. Downstream readers (`ls`, `cost`, `view`) must handle it alongside hook-mode event kinds.
- Helpers (`_data_root`, `_session_dir`, `_append_event`, `_write_state`, `_write_cost`, `_isoformat`, `_truncate`, `_log_self`) are intentionally duplicated from `capture.py` rather than shared — the two modules have inverted failure contracts that will diverge over time. A shared `_io.py` extraction is deferred to v0.2+.
- Does not touch the hook-handler capture hot path (`capture.py` is unmodified).

## How to Use

### CLI Commands

```bash
# Ingest a single SDK run file
agentlog tail agents/1b4319ab/researcher/cc_raw_output.jsonl

# Walk a directory and ingest all cc_raw_output.jsonl files found (depth ≤ 5)
agentlog tail agents/1b4319ab/

# Preview what would be ingested without writing anything
agentlog tail --dry-run agents/1b4319ab/researcher/cc_raw_output.jsonl

# Assign an explicit run id (verbatim, no sdk- prefix applied)
agentlog tail --run-id my-run-001 agents/1b4319ab/researcher/cc_raw_output.jsonl

# Re-ingest an already-ingested file (replaces the three known output files)
agentlog tail --force agents/1b4319ab/researcher/cc_raw_output.jsonl

# Label a run with a human-readable name stored in state.json
agentlog tail --source-name "phase-3-researcher" agents/1b4319ab/researcher/cc_raw_output.jsonl
```

### Programmatic Usage

```python
from pathlib import Path
from agentlog import tail

rc = tail.run_tail(
    Path("agents/1b4319ab/researcher/cc_raw_output.jsonl"),
    run_id=None,       # auto-derived from session_id in file
    source_name=None,  # defaults to filename
    dry_run=False,
    force=False,
)
# rc: 0 = success, 1 = I/O error, 2 = user error
```

## Configuration

| Environment variable | Default           | Effect                                      |
|----------------------|-------------------|---------------------------------------------|
| `AGENTLOG_HOME`      | `~/.agentlog/`    | Root directory for all `runs/<id>/` output  |

No other configuration required. Zero runtime dependencies beyond the Python 3.11+ standard library.

## Testing

```bash
# Run the tail-specific test suite
pytest tests/test_tail.py -q

# Run the full suite to confirm no regressions
pytest -q

# Type-check and lint
mypy --strict src tests
ruff check src tests
```

The test suite uses `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` to isolate all writes from the real `~/.agentlog/`. The `tests/fixtures/sdk_minimal.jsonl` fixture is a stable, hand-written 6-record stream-json file covering all major record types.

## Notes

- **No new runtime dependencies.** `pyproject.toml dependencies = []` remains empty. All imports are stdlib: `argparse`, `hashlib`, `json`, `os`, `pathlib`, `sys`, `datetime`, `typing`.
- **Local-first.** Zero network calls — reads local files, writes to `$AGENTLOG_HOME`. Satisfies CLAUDE.md hard rule #6.
- **Schema discipline.** Every JSONL record carries `schema_version: 1`. Unknown and unparseable records produce an `event: "unknown"` row with the raw payload. Satisfies CLAUDE.md hard rule #7.
- **v0.1 limitations (deferred, not bugs):**
  - Tool-use back-fill: `tool_use` events ship with `result_summary: null` and `duration_ms: null`; back-filling from the paired `user.tool_result` record is deferred to v0.2+.
  - `assistant.thinking` blocks are skipped entirely in v0.1.
  - `user` records containing only `tool_result` content are skipped (back-fill is v0.2+).
  - No live `tail -f` mode (v0.2+).
- **Privacy.** `tail` reads files the user already has on disk and writes only to a directory under the user's `$HOME`. No data leaves the machine.
