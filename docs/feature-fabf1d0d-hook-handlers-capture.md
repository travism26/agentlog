# Hook Handler Bodies — Capture Logic

**ADW ID:** fabf1d0d
**Date:** 2026-05-27
**Specification:** specs/feature-fabf1d0d-hook-handlers-capture.md

## Overview

Replaces the no-op `lambda args: 0` placeholder in the `agentlog _hook` subparser with real capture logic. Every Claude Code hook event (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`) is now read from stdin, parsed, and written to `~/.agentlog/runs/<session_id>/` as structured JSONL. This is the foundational write path that all downstream read-time commands (`ls`, `cost`, `view`) depend on.

## What Was Built

- `src/agentlog/_constants.py` — single source of truth for shared constants previously scattered in `hooks_install.py`
- `src/agentlog/capture.py` — stdlib-only capture module with fail-open boundary, per-event recorders, and atomic file writers
- `src/agentlog/cli.py` — wired `_run_hook` dispatcher replacing the lambda placeholder
- `src/agentlog/hooks_install.py` — refactored to re-export constants from `_constants.py`
- `tests/test_capture.py` — unit + integration test suite covering all event types and edge cases
- `tests/test_handler_perf.py` — optional cold-start and steady-state latency assertions

## Technical Implementation

### Files Modified

- `src/agentlog/cli.py`: Added `capture` import; replaced `lambda args: 0` with `_run_hook` dispatcher function
- `src/agentlog/hooks_install.py`: Replaced inline `EVENTS` and `HOOK_COMMAND_PREFIX` definitions with re-exports from `_constants.py`

### New Files

- `src/agentlog/_constants.py`: Exports `EVENTS`, `HOOK_COMMAND_PREFIX`, `SCHEMA_VERSION`, `SOURCE_HOOKS`, `MAX_INLINE_BYTES`, `DEFAULT_DATA_ROOT_NAME`, `SELF_LOG_NAME`, `RUNS_DIR_NAME`, `UNKNOWN_SESSION_PREFIX`
- `src/agentlog/capture.py`: Public surface `dispatch()` and `run_hook()`; private per-event recorders and file I/O helpers
- `tests/test_capture.py`: Pytest suite using `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", ...)` for filesystem isolation
- `tests/test_handler_perf.py`: `time.perf_counter` budget assertions (<50ms cold-start, <10ms steady-state)

### Key Changes

- **Fail-open boundary in `run_hook`**: every code path is wrapped in `try/except Exception` that logs to `_self.log` and returns 0 — a buggy handler cannot break a Claude Code session (CLAUDE.md hard rule #2)
- **Atomic file writes**: `state.json` and `cost.json` use `temp + os.replace` to prevent half-written files on crash or concurrent writes
- **`MAX_INLINE_BYTES = 4096`** truncation cap on prompt text and tool I/O keeps JSONL records under POSIX `PIPE_BUF` for safe concurrent appends to `events.jsonl`
- **Schema-drift tolerance**: unknown event names fall through to `_on_unknown`, which writes `event: "unknown"` + `raw: <full payload>` rather than crashing (CLAUDE.md hard rule #7)
- **Injectable `now` parameter** on `dispatch()` decouples tests from the real clock

### Architecture Impact

The `run_hook` / `dispatch` path runs in Claude Code's hot path on every tool call. Hot-path discipline enforced:

- One `datetime.now(UTC)` call per invocation, passed through to all recorders
- One `os.environ.get("AGENTLOG_HOME")` lookup per call — no module-level caching so tests can monkeypatch per-test
- `mkdir(parents=True, exist_ok=True)` called lazily per recorder, not at import time
- `events.jsonl` opened in `'a'` mode per invocation (fresh process per hook call, no persistent fd)
- Zero network calls anywhere in the hot path (no `urllib`, `http`, `socket`, `requests`, or `httpx` imports)

The on-disk schema locked by this task (`events.jsonl`, `state.json`, `cost.json`) is the wire format consumed by `agentlog tail` (item #3), `agentlog ls` (#4), `agentlog cost` (#5), and `agentlog view` (#6). Any future shape change requires a `schema_version` bump.

## How to Use

### CLI Commands

The `_hook` subparser is an internal command invoked by Claude Code's hook system. After `agentlog init` installs the hooks, Claude Code calls these automatically:

```bash
# Smoke test — must exit 0
echo '{}' | agentlog _hook SessionStart; echo $?

# Malformed input — still exits 0, writes to _self.log
echo 'not json' | agentlog _hook SessionStart; echo $?

# Full session capture to a custom root
AGENTLOG_HOME=/tmp/al-smoke
echo '{"session_id":"smoke"}' | agentlog _hook SessionStart
# Creates: /tmp/al-smoke/runs/smoke/state.json and events.jsonl
```

### Programmatic Usage

```python
import io
from agentlog import capture

# dispatch() is the pure entry point (injectable now for tests)
from datetime import datetime, UTC
rc = capture.dispatch("SessionStart", {"session_id": "abc", "cwd": "/home/user", "model": "claude-opus-4-7"})

# run_hook() reads from sys.stdin — monkeypatch for tests
import sys
sys.stdin = io.StringIO('{"session_id":"abc"}')
rc = capture.run_hook("SessionStart")  # returns 0
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `AGENTLOG_HOME` | `~/.agentlog` | Override the data root for captured sessions |

## Testing

```bash
# Full suite
pytest

# Capture-specific tests only
pytest tests/test_capture.py

# Performance budget tests (non-blocking if slow on CI)
AGENTLOG_PERF=1 pytest tests/test_handler_perf.py
```

Key test patterns used:
- `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` for filesystem isolation — real `~/.agentlog/` is never touched
- `monkeypatch.setattr(sys, "stdin", io.StringIO(...))` for stdin injection
- Injectable `now=datetime(2026, 5, 27, ...)` parameter on `dispatch()` for deterministic timestamp assertions

## Notes

- **Wire format is locked.** The JSONL record shapes in `events.jsonl`, `state.json`, and `cost.json` are the contract for all downstream v0.1 commands. Changes require `schema_version: 2`, not a silent field rename.
- **`source: "hooks"` discriminator** is forward-looking: `agentlog tail` (item #3) will write `source: "sdk"` records into the same schema; `agentlog ls` will use this field to label rows.
- **Privacy.** Prompt text and tool I/O are written to a local filesystem path only (`~/.agentlog/` by default). No network egress, no telemetry, no version checks. The 4KB truncation cap bounds data-at-rest size per event but does not redact secrets — secret redaction is a v0.2+ feature.
- **`PreToolUse` intentionally omitted.** Blocking hooks are deferred past v0.1 (CLAUDE.md hard rule #5). The `_DISPATCH` table covers exactly the five events in `EVENTS`.
- **`pyproject.toml` `dependencies = []` unchanged.** `capture.py` imports only stdlib: `json`, `os`, `sys`, `time`, `contextlib`, `pathlib`, `datetime`, `collections.abc`, `typing`, plus internal `agentlog._constants`.
