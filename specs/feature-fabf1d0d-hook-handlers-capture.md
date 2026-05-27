# Feature: Hook handler bodies (`agentlog _hook <Event>` capture logic)

## Metadata

adw_id: `fabf1d0d`
prompt: `/tmp/agentlog_step2_prompt.md`

## Feature Description

Implement v0.1 ship-scope item #2 from `DESIGN.md`: the real bodies behind the five
`agentlog _hook <Event>` invocations that ship-scope item #1 (commit `390bc7b`) already wired into
`~/.claude/settings.json`. Today the hidden `_hook` subparser in `src/agentlog/cli.py:59-64`
routes every event to `lambda args: 0` — a deliberate no-op placeholder. This feature replaces
that lambda with capture logic that reads Claude Code's hook payload from stdin and writes the
unified `runs/<session_id>/` schema described in `DESIGN.md` lines 109-131.

Five events are handled: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`.
`PreToolUse` is deliberately omitted (CLAUDE.md hard rule #5 defers it past v0.1). All work lives
in one new module (`src/agentlog/capture.py` — name dictated by `DESIGN.md:250`), a five-line
edit to `cli.py`, and a dedicated test suite. No new runtime dependencies; `pyproject.toml`
`dependencies = []` stays empty.

The on-disk JSONL schema defined in this task is a wire-format contract that three downstream
v0.1 features (`agentlog tail`, `agentlog ls`, `agentlog cost`, `agentlog view`) will consume —
field names and shape decisions made here must consider those future readers.

## User Story

As a developer using Claude Code with agentlog hooks installed
I want every interactive `claude` session to be captured to `~/.agentlog/runs/<session_id>/`
automatically and invisibly
So that I can later inspect cost, replay the timeline, and verify what the agent actually did —
without my Claude Code sessions ever being slowed down, crashed, or otherwise altered by
agentlog's presence.

## Problem Statement

Ship-scope item #1 registered hook handlers in `settings.json`, but those handlers are no-ops.
Until item #2 lands, `agentlog init` is functionally cosmetic: it mutates the user's
`settings.json` and produces zero observability. The capture layer is also the foundation every
other v0.1 read-time command (`ls`, `cost`, `view`) is built on; nothing downstream can be built
until the on-disk schema exists and is exercised by real sessions.

The handlers must satisfy a punishing set of constraints simultaneously:

- They run in Claude Code's hot path on **every** tool call (`PostToolUse` fires per Bash, per
  Read, per Edit, per Write). Latency directly degrades the user's session.
- They must never break Claude Code, even when the agentlog code itself is buggy, the disk is
  full, the data root is read-only, or Anthropic changes the hook payload shape unannounced.
- They define the JSONL record shape that three downstream features (and SDK-mode in item #3)
  will rely on as a stable wire format.

## Solution Statement

Add a single new stdlib-only module `src/agentlog/capture.py` exporting two public functions:

- `dispatch(event, payload, *, now=None) -> int` — pure-ish entry point that takes an event name
  and a parsed payload dict, writes the appropriate event record(s) to
  `~/.agentlog/runs/<session_id>/`, updates `state.json` / `cost.json` as appropriate, and
  returns `0`. The `now` parameter is injectable for deterministic tests.
- `run_hook(event) -> int` — CLI-facing entry point. Reads JSON from stdin, parses it, calls
  `dispatch`, and returns `0`. **Every code path is wrapped in a top-level `try/except Exception`
  that logs to `~/.agentlog/_self.log` and returns 0 anyway** — CLAUDE.md hard rule #2 fail-open.

`cli.py` is patched to replace the `lambda args: 0` on line 61 with `func=_run_hook`, where
`_run_hook` is a thin wrapper that calls `capture.run_hook(args.event)`. The `_choices_actions`
filter on line 64 (which hides `_hook` from `--help`) stays untouched.

The shared constants `EVENTS` and `HOOK_COMMAND_PREFIX` move into a new tiny
`src/agentlog/_constants.py`. Both `hooks_install.py` and `capture.py` import from it. This keeps
`capture.py`'s import graph minimal (a stale-cache cold-start concern — see research §Risks #2)
without duplicating the source-of-truth tuple. `hooks_install.py` re-exports `EVENTS` and
`HOOK_COMMAND_PREFIX` so existing imports keep working.

The JSONL wire format is documented inline (Phase 2 §Event-record shapes below) and locked by
this task.

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` (lines 109-131, 137-159, 218-229, 245-252) — locked schema, performance contract,
  schema-drift risk row, code-provenance mapping.
- `CLAUDE.md` (hard rules #1, #2, #5, #6, #7, #8) — non-negotiable constraints applied as
  acceptance gates.
- `ai_docs/research/fabf1d0d-hook-handlers-capture-analysis.md` — pre-planning research; contains
  module-surface recommendation, event-record shape proposals, risk catalogue, and test plan
  that this spec follows.
- `src/agentlog/cli.py` (lines 59-66) — the integration seam. The lambda on line 61 is replaced;
  the `_choices_actions` filter on line 64 must survive.
- `src/agentlog/hooks_install.py` — read-only here. `EVENTS` (line 34) and `HOOK_COMMAND_PREFIX`
  (line 42) move to `_constants.py`; this module re-exports them for backward compatibility.
- `src/agentlog/__init__.py` — no changes expected (already exposes `__version__`).
- `tests/test_cli_smoke.py` — must continue to pass unmodified.
- `tests/test_hooks_install.py` (lines 430-438) — `test_hook_noop_subparser_exits_zero` must
  still pass: `run_hook` returning 0 on empty stdin is the fail-open path that satisfies it.
- `pyproject.toml` (line 28) — `dependencies = []` MUST stay empty. mypy strict + ruff
  (`E/W/F/I/B/UP/SIM`) gates must remain green.

### New Files

- `src/agentlog/_constants.py` — single source of truth for `EVENTS` and `HOOK_COMMAND_PREFIX`.
  Tiny module, imported by both `hooks_install.py` (re-export) and `capture.py`.
- `src/agentlog/capture.py` — the implementation. Public surface: `dispatch`, `run_hook`. Private
  per-event recorders + helpers. Stdlib-only imports: `json`, `os`, `sys`, `time`, `pathlib`,
  `datetime`, `typing` only.
- `tests/test_capture.py` — unit + integration coverage for `dispatch`, `run_hook`, and every
  per-event recorder. Uses `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` for
  filesystem isolation. Never touches real `~/.agentlog/`.
- `tests/test_handler_perf.py` (optional, recommended) — `time.perf_counter` assertions for the
  <50ms cold and <10ms steady budgets. Per the prompt, budget failures are `tech_debt`-class
  (non-blocking). Skip on CI runners under heavy load via an env-guard if flaky.

## Implementation Plan

### Phase 1: Foundation

Lay the structural groundwork so the actual capture code has clean seams.

1. **Extract shared constants.** Move `EVENTS` and `HOOK_COMMAND_PREFIX` (and `agentlog_command`
   helper, optionally) from `hooks_install.py` into a new `src/agentlog/_constants.py`.
   `hooks_install.py` re-imports and re-exports them so external callers (and the existing test
   suite) see no behavioural change.
2. **Decide truncation limit.** Set `MAX_INLINE_BYTES = 4096` in `_constants.py` (or in
   `capture.py` directly — see research §Risks #5). 4KB keeps the common case under POSIX
   `PIPE_BUF` so concurrent writes interleave cleanly; oversize records get `truncated_bytes: N`
   set and content clipped before `json.dumps`.
3. **Decide the schema version constant.** `SCHEMA_VERSION = 1` (integer), placed at the top of
   every JSONL record and every JSON file. CLAUDE.md hard rule #7.

### Phase 2: Core Implementation

Build `src/agentlog/capture.py`. Stdlib-only. Layering: pure helpers → per-event recorders →
`dispatch` orchestrator → `run_hook` fail-open boundary.

#### Module surface

```python
# constants
SCHEMA_VERSION: int = 1
SOURCE_HOOKS: str = "hooks"
SELF_LOG_NAME: str = "_self.log"
RUNS_DIR_NAME: str = "runs"
UNKNOWN_SESSION_PREFIX: str = "unknown_session"
MAX_INLINE_BYTES: int = 4096
DEFAULT_DATA_ROOT_NAME: str = ".agentlog"

# public surface
def dispatch(event: str, payload: dict, *, now: datetime | None = None) -> int: ...
def run_hook(event: str) -> int: ...

# per-event recorders (private)
def _on_session_start(payload, now, session_dir, session_id) -> None: ...
def _on_user_prompt_submit(payload, now, session_dir, session_id) -> None: ...
def _on_post_tool_use(payload, now, session_dir, session_id) -> None: ...
def _on_stop(payload, now, session_dir, session_id) -> None: ...
def _on_session_end(payload, now, session_dir, session_id) -> None: ...
def _on_unknown(event, payload, now, session_dir, session_id) -> None: ...

# helpers
def _data_root() -> Path                                # honors AGENTLOG_HOME; never cached
def _session_dir(root: Path, session_id: str) -> Path
def _resolve_session_id(payload: dict) -> str           # extracts or falls back
def _append_event(session_dir: Path, record: dict) -> None    # 'a' mode, single json.dumps line
def _write_state(session_dir: Path, state: dict) -> None      # temp + os.replace (atomic)
def _write_cost(session_dir: Path, cost: dict) -> None        # temp + os.replace
def _read_json(path: Path) -> dict                            # returns {} on missing/malformed
def _log_self(root: Path, message: str) -> None               # appends to _self.log; swallows
def _truncate(value: str, limit: int) -> tuple[str, int]      # (clipped, dropped_bytes)
def _isoformat(dt: datetime) -> str                           # UTC + offset, lexically sortable
def _fallback_session_id() -> str                             # unknown_session-<pid>-<unix_ms>
```

#### Event-record shapes (locks the wire format)

Every record on `events.jsonl` carries these common fields: `schema_version: 1`, `event: <name>`,
`timestamp: <iso-8601 UTC>`, `session_id: <id>`, `source: "hooks"`. Event-specific fields:

```jsonc
// SessionStart
{"schema_version":1,"event":"session_start","timestamp":"2026-05-27T12:34:56+00:00",
 "session_id":"abc","source":"hooks","parent_session_id":null,"cwd":"...","model":"..."}

// UserPromptSubmit
{"schema_version":1,"event":"prompt","timestamp":"...","session_id":"abc","source":"hooks",
 "text":"...","text_bytes":1234,"truncated_bytes":0}

// PostToolUse
{"schema_version":1,"event":"tool_use","timestamp":"...","session_id":"abc","source":"hooks",
 "tool":"Bash","params_summary":"...","result_summary":"...","duration_ms":null,
 "truncated_bytes":0}

// Stop
{"schema_version":1,"event":"stop","timestamp":"...","session_id":"abc","source":"hooks",
 "usage":{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_creation_tokens":0}}

// SessionEnd
{"schema_version":1,"event":"session_end","timestamp":"...","session_id":"abc","source":"hooks",
 "summary":null}

// Unknown event name (schema drift tolerance)
{"schema_version":1,"event":"unknown","timestamp":"...","session_id":"abc","source":"hooks",
 "original_event":"WhateverNewName","raw":{...full payload...}}
```

`state.json` (whole-file, atomic rewrite via temp + `os.replace`; last-writer-wins on races):

```jsonc
{"schema_version":1,"session_id":"abc","parent_session_id":null,
 "started_at":"...","ended_at":null,"cwd":"...","model":"...",
 "event_count":0,"source":"hooks","summary":null}
```

`cost.json`:

```jsonc
{"schema_version":1,"session_id":"abc",
 "totals":{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_creation_tokens":0},
 "phases":{}}
```

#### The fail-open boundary

```python
def run_hook(event: str) -> int:
    root = _data_root()
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _log_self(root, f"empty stdin on {event}")
            return 0
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log_self(root, f"malformed JSON on {event}: {exc}")
            return 0
        if not isinstance(payload, dict):
            _log_self(root, f"non-object payload on {event}: {type(payload).__name__}")
            return 0
        return dispatch(event, payload)
    except Exception as exc:  # noqa: BLE001 — fail-open per CLAUDE.md hard rule #2
        try:
            _log_self(root, f"unhandled in {event}: {exc!r}")
        except Exception:
            pass
        return 0
```

`_log_self` itself wraps its body in `try/except Exception: pass` so a read-only `_self.log`
cannot resurrect a propagating exception.

#### Hot-path discipline

- One `datetime.now(timezone.utc)` per handler call. Pass `now` through to recorders.
- One `os.environ.get("AGENTLOG_HOME")` lookup per call (no module-level caching — tests
  monkeypatch per-test).
- `mkdir(parents=True, exist_ok=True)` is idempotent and ~tens-of-µs; call it from each recorder
  so `SessionEnd`-before-`SessionStart` and unknown-event paths still work.
- `json.dumps(record, separators=(",", ":"))` — compact, faster than pretty-printing, fits PIPE_BUF.
- Truncate raw blobs **before** `json.dumps`. A 10MB tool result would otherwise dominate cost.
- Open `events.jsonl` in `'a'` mode for each call — fresh process each invocation; no long-lived fd.

### Phase 3: Integration

Wire `capture.run_hook` into the CLI and verify it composes with the item-#1 install path.

1. **Edit `src/agentlog/cli.py`.**
   - Add `from agentlog import capture` to the top imports (line 9 region). Direct import is fine
     — `capture.py` is stdlib-only and its import cost is negligible (no heavy modules pulled in).
     If perf benchmarking later shows this dominates cold-start, switch to a lazy import inside
     `_run_hook`.
   - Replace `hook_sp.set_defaults(func=lambda args: 0)` on line 61 with
     `hook_sp.set_defaults(func=_run_hook)`.
   - Add the `_run_hook` function near `_run_init` / `_run_uninstall`:
     ```python
     def _run_hook(args: argparse.Namespace) -> int:
         return capture.run_hook(args.event)
     ```
   - Leave the `_choices_actions` filter on line 64 untouched.

2. **Confirm `hooks_install.py` still exposes `EVENTS` and `HOOK_COMMAND_PREFIX`.** Either keep
   them as `from agentlog._constants import EVENTS, HOOK_COMMAND_PREFIX  # noqa: F401` re-exports,
   or keep the originals there and have `_constants.py` import FROM `hooks_install.py`. Prefer the
   former — `_constants.py` is the source of truth, `hooks_install.py` re-exports.

3. **Manual smoke test.**
   - `echo '{}' | .venv/bin/agentlog _hook SessionStart; echo $?` → prints `0`.
   - `echo 'not json' | .venv/bin/agentlog _hook SessionStart; echo $?` → prints `0`, and
     `~/.agentlog/_self.log` (or `$AGENTLOG_HOME/_self.log` if set) gains a line.
   - `AGENTLOG_HOME=/tmp/al-smoke echo '{"session_id":"smoke"}' | .venv/bin/agentlog _hook
     SessionStart` → creates `/tmp/al-smoke/runs/smoke/state.json` + `events.jsonl`.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Extract shared constants into `_constants.py`

- Create `src/agentlog/_constants.py` with `EVENTS`, `HOOK_COMMAND_PREFIX`, `SCHEMA_VERSION`,
  `SOURCE_HOOKS`, `MAX_INLINE_BYTES`, `DEFAULT_DATA_ROOT_NAME`, `SELF_LOG_NAME`, `RUNS_DIR_NAME`,
  `UNKNOWN_SESSION_PREFIX`.
- Replace the literal `EVENTS` and `HOOK_COMMAND_PREFIX` definitions at
  `hooks_install.py:34` and `hooks_install.py:42` with `from agentlog._constants import EVENTS,
  HOOK_COMMAND_PREFIX  # noqa: F401`. Move the docstring note about
  `HOOK_COMMAND_PREFIX` being part of the installed-file format into `_constants.py` so it's
  preserved.
- Run the existing test suite (`pytest`) — every existing test must still pass without changes
  before any new code is added.

### 2. Add helper functions in `capture.py`

- Create `src/agentlog/capture.py` skeleton with `from __future__ import annotations`, stdlib-only
  imports, and the module docstring.
- Implement `_data_root()` — reads `AGENTLOG_HOME` env var each call, defaults to
  `Path.home() / ".agentlog"`. No caching.
- Implement `_isoformat(dt)` — `dt.astimezone(timezone.utc).isoformat(timespec="microseconds")`
  or similar; ensure trailing `+00:00`. Never `datetime.utcnow()` (deprecation-track in 3.12+).
- Implement `_fallback_session_id()` — `f"unknown_session-{os.getpid()}-{int(time.time()*1000)}"`.
- Implement `_session_dir(root, session_id)` — returns `root / "runs" / session_id`. No mkdir
  side effect; recorders mkdir lazily.
- Implement `_truncate(value, limit)` — UTF-8-byte-aware. If `len(value.encode("utf-8")) <= limit`
  return `(value, 0)`; else clip to `limit` bytes (decoding back safely on a codepoint boundary)
  and return `(clipped, dropped_bytes)`.
- Implement `_resolve_session_id(payload)` — returns `payload.get("session_id")` if it's a
  non-empty string, else `_fallback_session_id()`.
- Implement `_log_self(root, message)` — opens `root / "_self.log"` in `'a'` mode, prepends ISO
  timestamp, writes one line. Entire body in `try/except Exception: pass`.

### 3. Add atomic file writers and event appender

- Implement `_append_event(session_dir, record)` — `session_dir.mkdir(parents=True, exist_ok=True)`,
  open `session_dir / "events.jsonl"` in `'a'` mode with `encoding="utf-8"`, write
  `json.dumps(record, separators=(",", ":")) + "\n"`. POSIX append atomicity covers <PIPE_BUF
  records; the `MAX_INLINE_BYTES` cap keeps records under that ceiling.
- Implement `_write_state(session_dir, state)` and `_write_cost(session_dir, cost)` — both
  whole-file writers using temp + `os.replace` (mirror `hooks_install.write_atomic`'s pattern,
  but with `sort_keys=False` since these aren't human-edited).
- Implement `_read_json(path)` — returns `{}` on `FileNotFoundError` or any
  `json.JSONDecodeError`; logs the JSONDecodeError case to `_self.log` (called with the data root
  passed in).

### 4. Add per-event recorders

- `_on_session_start(payload, now, session_dir, session_id)`:
  - `mkdir parents=True, exist_ok=True`.
  - Write initial `state.json` via `_write_state`: `started_at = _isoformat(now)`,
    `parent_session_id = payload.get("parent_session_id")`, `cwd = payload.get("cwd")`,
    `model = payload.get("model")`, `event_count = 0`, `summary = None`, `source = "hooks"`,
    `schema_version = 1`.
  - Append a `session_start` event with those same fields.
- `_on_user_prompt_submit(payload, now, session_dir, session_id)`:
  - Extract prompt text from a list of candidate keys (`prompt`, `text`, `user_prompt`).
    Per research §Integration #1, treat the payload as opaque except documented fields; if no
    candidate matches, fall back to `json.dumps(payload, separators=…)` as the `text`.
  - `text_bytes = len(raw_text.encode("utf-8"))`.
  - `clipped, truncated_bytes = _truncate(raw_text, MAX_INLINE_BYTES)`.
  - Append `prompt` event.
- `_on_post_tool_use(payload, now, session_dir, session_id)`:
  - Extract `tool = payload.get("tool_name") or payload.get("tool") or "unknown"`.
  - Build `params_summary` and `result_summary` by `json.dumps`-ing the relevant subfields
    (`tool_input`, `tool_response`, etc.) and truncating each via `_truncate`.
  - Set `duration_ms = payload.get("duration_ms")` if present, else `None`.
  - Sum `truncated_bytes` across both summaries.
  - Append `tool_use` event.
- `_on_stop(payload, now, session_dir, session_id)`:
  - Extract `usage = payload.get("usage") or {}`.
  - Append `stop` event with `usage` subfield containing the raw token counts (defaulting each
    to `0`).
  - Read existing `cost.json` via `_read_json`; create-if-missing with schema-conformant
    defaults; add the four token counts into `totals.*`; write back via `_write_cost`.
- `_on_session_end(payload, now, session_dir, session_id)`:
  - `mkdir parents=True, exist_ok=True` (handles `SessionEnd`-before-`SessionStart`).
  - Append `session_end` event.
  - Read existing `state.json` (default if missing); set `ended_at = _isoformat(now)`; recompute
    `event_count` by counting lines in `events.jsonl` (one pass, no JSON parsing); set
    `summary = payload.get("summary")` if present. Write back via `_write_state`.
- `_on_unknown(event, payload, now, session_dir, session_id)`:
  - Append a single `unknown` event carrying `original_event=event` and `raw=payload` (with the
    raw payload's JSON serialization truncated to `MAX_INLINE_BYTES` if oversize).

### 5. Implement `dispatch` and `run_hook`

- `dispatch(event, payload, *, now=None) -> int`:
  - `now = now or datetime.now(timezone.utc)`.
  - `session_id = _resolve_session_id(payload)`.
  - `root = _data_root()`.
  - `session_dir = _session_dir(root, session_id)`.
  - Dispatch table (`dict[str, Callable]` mapping `EVENTS` → recorder). Unknown event names fall
    through to `_on_unknown(event, …)`.
  - Return `0`.
- `run_hook(event) -> int`: implement exactly the fail-open boundary shown in Phase 2 above. Read
  stdin once, parse, dispatch, swallow every `Exception` to `_self.log`, return 0.

### 6. Wire into the CLI

- Edit `src/agentlog/cli.py`:
  - Update the top import to `from agentlog import __version__, capture, hooks_install` (keep
    `hooks_install` for `_run_init` / `_run_uninstall`).
  - Replace `hook_sp.set_defaults(func=lambda args: 0)` (line 61) with
    `hook_sp.set_defaults(func=_run_hook)`.
  - Add `_run_hook(args: argparse.Namespace) -> int: return capture.run_hook(args.event)` near
    the other underscore-prefixed CLI dispatchers.
  - Leave the `_choices_actions` filter on line 64 untouched.

### 7. Create `tests/test_capture.py`

- Add the test cases enumerated in §Testing Strategy below. Use `tmp_path` and
  `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` for isolation. Never touch the real
  `~/.agentlog/` directory.
- Mirror `tests/test_hooks_install.py` style: `from __future__ import annotations`, explicit
  return types on test functions (mypy strict applies to tests too per `pyproject.toml:84`),
  `parametrize` for table-driven cases, `capsys` for stdout/stderr capture where useful.

### 8. (Optional, recommended) Create `tests/test_handler_perf.py`

- Use `time.perf_counter()` deltas around `run_hook` calls. Two tests:
  - Cold start (fresh `tmp_path`, first `SessionStart` call) — assert <50ms.
  - Steady state (run dir already exists, append `PostToolUse`) — assert <10ms.
- Per the prompt, treat budget failures as `tech_debt` — non-blocking. If CI is too noisy, gate
  on `AGENTLOG_PERF=1` env var or mark with `@pytest.mark.perf` and exclude from default runs.

### 9. Run the gates

- `pytest` — all existing 36+ new tests pass.
- `ruff check src tests` — clean.
- `mypy --strict src tests` (or just `mypy` since `[tool.mypy].strict = true`) — clean.
- Manual smoke: the three commands listed in Phase 3 §3.
- Verify `~/.agentlog/` not created by the test suite (tests use `AGENTLOG_HOME` redirection).

### 10. Commit

- One commit containing: `_constants.py` (new), `hooks_install.py` (re-export edit),
  `capture.py` (new), `cli.py` (wire-up edit), `tests/test_capture.py` (new),
  optionally `tests/test_handler_perf.py` (new). NO change to `pyproject.toml`, README, or
  `~/.claude/settings.json`. Commit message focuses on the *why* (replace no-op `_hook` lambda
  with capture per ship-scope item #2). NEVER use `--amend` or `--no-verify`.

## Testing Strategy

**IMPORTANT**: No project-specific testing doc (`HOW_TO_CREATE_TESTS.md` / `TESTING.md`) was
found in `tests/` or at the project root. Follow the conventions established by
`tests/test_hooks_install.py` and `tests/test_cli_smoke.py`: pytest with `tmp_path`,
`monkeypatch.setenv` for env isolation, `parametrize` for table-driven cases, no hardcoded
paths (always derive from `tmp_path` or `monkeypatch`).

### Unit Tests

For `tests/test_capture.py`:

- `test_dispatch_session_start_writes_state_and_event` — happy path; `state.json` populated with
  expected fields, `events.jsonl` has exactly 1 record with `event=="session_start"`, every
  field schema-conformant (`schema_version==1`, `source=="hooks"`, ISO timestamp ends with
  `+00:00`).
- `test_dispatch_user_prompt_submit_appends_prompt_event` — prompt text captured, `text_bytes`
  matches `len(text.encode("utf-8"))`, `truncated_bytes==0` for short inputs.
- `test_dispatch_post_tool_use_records_tool_and_summaries` — `tool` extracted from `tool_name`
  field; `params_summary` and `result_summary` truncated to `MAX_INLINE_BYTES`; `duration_ms`
  preserved when present.
- `test_dispatch_stop_updates_cost_json` — `cost.json.totals` populated from `payload["usage"]`;
  multiple `Stop` events accumulate (not overwrite) when called twice.
- `test_dispatch_session_end_finalises_state` — `ended_at` set, `event_count` reflects
  `events.jsonl` line count (verify by appending some events first).
- `test_dispatch_unknown_event_writes_generic_record` — `event=="unknown"`, `original_event` is
  the Anthropic event name, `raw` field contains the full payload, exit 0.
- `test_run_hook_malformed_stdin_logs_and_returns_zero` — `monkeypatch sys.stdin` to
  `io.StringIO("not json")`; verify `_self.log` grew with a "malformed JSON" line, no `runs/`
  dir created, rc==0.
- `test_run_hook_empty_stdin_returns_zero` — same with empty / whitespace-only stdin; distinct
  `_self.log` line.
- `test_run_hook_non_object_payload_returns_zero` — JSON array/string at top level.
- `test_run_hook_missing_session_id_uses_fallback` — payload lacks `session_id`; record lands
  under `runs/unknown_session-<pid>-<ms>/`.
- `test_run_hook_read_only_root_returns_zero` — `tmp_path.chmod(0o500)`; dispatch a SessionStart;
  rc==0 and no exception escapes. (Skip on Windows; tests already implicitly POSIX-only here.)
- `test_session_end_before_session_start_tolerated` — call `dispatch("SessionEnd", …)` against
  a session that never had `SessionStart`; run dir created lazily; `state.json` initialised with
  reasonable defaults; no crash.
- `test_truncation_records_truncated_bytes_field` — feed a prompt of length `MAX_INLINE_BYTES+1
  KB`; `truncated_bytes` is positive, `text` is clipped.
- `test_schema_version_is_one_on_every_record` — parametrize across all event types; assert each
  JSONL line and each JSON file has top-level `schema_version: 1`.
- `test_agentlog_home_env_var_redirects_root` — `monkeypatch.setenv("AGENTLOG_HOME", str(tmp))`;
  assert files land under `tmp`, not under `~/.agentlog`.
- `test_self_log_write_failure_swallowed` — make `_self.log` unwritable AND force a dispatch
  error; verify rc==0 still (the inner `_log_self` try/except is exercised).

For `_constants.py` extraction:

- The existing `tests/test_hooks_install.py` suite must keep passing without edits. Add a
  `test_constants_module_exports_events_and_prefix` smoke that asserts
  `from agentlog._constants import EVENTS, HOOK_COMMAND_PREFIX` works and that
  `hooks_install.EVENTS is _constants.EVENTS` (identity, not just equality).

### Integration Tests

- `test_run_hook_end_to_end_session_lifecycle` — drive `run_hook` for a realistic sequence:
  `SessionStart` → `UserPromptSubmit` → `PostToolUse` ×3 → `Stop` → `SessionEnd`. Assert
  `events.jsonl` has 7 records in the correct order, `state.json.event_count == 7`,
  `state.json.ended_at` populated, `cost.json.totals` reflects the `Stop` payload's `usage`
  block.
- `test_cli_invokes_capture` — call `agentlog.cli.main(["_hook", "SessionStart"])` with a piped
  stdin (use `monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"x"}'))`); assert
  rc==0 and `tmp_path/runs/x/state.json` exists. This is the only test that crosses the
  CLI seam end-to-end.
- The existing `test_hook_noop_subparser_exits_zero` test in `tests/test_hooks_install.py:430`
  must keep passing — it asserts the wired subparser returns 0; our `run_hook` returns 0 on
  empty stdin, which is the fail-open path it exercises.

### Edge Cases

- Malformed JSON on stdin → `_self.log` line + rc==0, no `runs/` dir.
- Empty / whitespace-only stdin → `_self.log` line + rc==0.
- Non-object JSON (array, string, number) at top level → `_self.log` line + rc==0.
- Unknown event name (e.g. `agentlog _hook FuturePreTaskUse`) → `event="unknown"` record written,
  `original_event` preserved, `raw` payload preserved.
- `~/.agentlog/` doesn't exist → created on first `SessionStart`.
- `~/.agentlog/` read-only → rc==0 (no crash), best-effort `_self.log` attempt may also fail (and
  is swallowed).
- Hook payload missing `session_id` → fallback `unknown_session-<pid>-<unix_ms>` dir created;
  data not dropped.
- `SessionEnd` arrives before `SessionStart` (out-of-order) → run dir created lazily; `state.json`
  initialised with `started_at = None`, `ended_at = <now>`.
- Very large prompt / tool result (>MAX_INLINE_BYTES) → truncated; `truncated_bytes` field
  records dropped count; record stays <PIPE_BUF.
- Concurrent handler invocations writing to the same `events.jsonl` → POSIX `'a'` mode atomicity
  for <PIPE_BUF writes (documented; not tested as flaky; do not add a flock).
- Multi-byte UTF-8 truncation boundary — `_truncate` must not split a codepoint mid-byte
  (test with a string of emoji at exactly `MAX_INLINE_BYTES-1`).
- `datetime.now()` injection — pass `now=datetime(2026, 5, 27, …)` and assert serialized
  timestamps match expected ISO string (no real-clock dependency in tests).

## Acceptance Criteria

- Running a real `claude` session with `agentlog init` already installed produces
  `~/.agentlog/runs/<session_id>/{state.json,events.jsonl,cost.json}` populated with: session
  metadata, one `prompt` record per user prompt, one `tool_use` record per `PostToolUse`, a
  `stop` record with token totals, and a `session_end` record on exit.
- `echo '{}' | agentlog _hook SessionStart; echo $?` prints `0`.
- `echo 'not json' | agentlog _hook SessionStart; echo $?` prints `0`, and
  `$AGENTLOG_HOME/_self.log` (or `~/.agentlog/_self.log`) gains a line.
- `AGENTLOG_HOME=/tmp/al-smoke echo '{"session_id":"smoke"}' | agentlog _hook SessionStart`
  creates `/tmp/al-smoke/runs/smoke/state.json` and `events.jsonl`.
- Every JSONL record and every JSON file has top-level `schema_version: 1` (CLAUDE.md hard
  rule #7).
- Every `agentlog _hook <Event>` invocation exits 0, including all fault-injected paths
  (malformed stdin, missing session_id, read-only data root, unknown event name).
- Cold-start handler wall time <50ms; steady-state <10ms (asserted by `tests/test_handler_perf.py`
  when run; failures are non-blocking per the prompt's "tech_debt" classification).
- Existing 36 tests still pass; 15+ new tests added; ruff and mypy strict both clean.
- `pyproject.toml` `dependencies = []` unchanged.
- No network calls anywhere in the handler hot path (CLAUDE.md hard rule #1, #6); verifiable by
  grep for `urllib`/`http`/`socket`/`requests`/`httpx` returning zero hits in `capture.py`.
- README NOT touched (docs for this feature are written separately into
  `docs/feature-fabf1d0d-hook-handlers.md` by the `/document` slash-command phase).
- `~/.claude/settings.json` NOT mutated by this task (only by `agentlog init`, which is item #1).

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors:

- `.venv/bin/python -m py_compile src/agentlog/_constants.py && echo OK` — Verify no syntax errors.
- `.venv/bin/python -m py_compile src/agentlog/capture.py && echo OK` — Verify no syntax errors.
- `.venv/bin/python -m py_compile src/agentlog/cli.py && echo OK` — Verify the CLI edit compiles.
- `.venv/bin/python -c "from agentlog import capture; print('import OK')"` — Verify
  stdlib-only imports resolve.
- `.venv/bin/python -c "from agentlog._constants import EVENTS, HOOK_COMMAND_PREFIX; print(EVENTS)"`
  — Verify constants module exposes the source-of-truth tuple.
- `.venv/bin/python -c "from agentlog import hooks_install; assert hooks_install.EVENTS == ('SessionStart','UserPromptSubmit','PostToolUse','Stop','SessionEnd'); print('re-export OK')"`
  — Verify `hooks_install` still exposes `EVENTS` for any external importer.
- `.venv/bin/agentlog --help` — Verify CLI still works AND `_hook` is still hidden from `--help`
  (output should NOT contain `_hook`).
- `echo '{}' | .venv/bin/agentlog _hook SessionStart; echo $?` — End-to-end smoke; must print
  `0`.

## Notes

- **Stdlib-only is non-negotiable.** No `uv add` invocations. `pyproject.toml`'s `dependencies`
  array stays `[]`. Allowed import set in `capture.py`: `json`, `os`, `sys`, `time`,
  `pathlib`, `datetime`, `typing`, plus internal `agentlog._constants`.
- **Privacy.** This is a local-first observability tool (CLAUDE.md hard rule #6). The handlers
  write prompt text and tool I/O to a local filesystem path that defaults to `~/.agentlog/`. No
  network egress, no telemetry, no version checks — anywhere in the hot path. The truncation
  cap (`MAX_INLINE_BYTES = 4096`) bounds the worst-case data-at-rest size per event but does NOT
  redact secrets; that's an explicit v0.2+ feature (mentioned in `DESIGN.md` privacy risk row).
- **Wire format is locked by this task.** The JSONL record shapes in Phase 2 §Event-record
  shapes are the contract `agentlog tail` (item #3), `ls` (#4), `cost` (#5), and `view` (#6) will
  read. Any change to the shape after this lands is a breaking change to consumers — version it
  (`schema_version: 2`) rather than silently mutating.
- **`source: "hooks"` on every record** is forward-looking. Item #3 (`agentlog tail`) will write
  records with `source: "sdk"` into the same `runs/<id>/` schema. Downstream readers (item #4
  `agentlog ls`) will use this discriminator to label rows.
- **`HOOK_COMMAND_PREFIX` is part of the installed-file format.** Already documented in
  `hooks_install.py:6-21`. Moving it to `_constants.py` preserves the value (do not rename, do
  not reformat). The docstring note about migration plans for any change should travel with the
  constant.
- **The hidden `_hook` subparser must stay hidden** — `cli.py:64`'s `_choices_actions` filter is
  fragile (it touches an argparse internal). Verify `agentlog --help` still does NOT list `_hook`
  after the wire-up.
- **Out of scope for this task** (deferred to later v0.1 items or v0.2+):
  `agentlog tail <dir>` (item #3), `agentlog ls` SQLite index (#4), `agentlog cost` rollup (#5 —
  this task writes raw token data; cost-per-phase math happens at read-time), `agentlog view`
  TUI (#6), README updates and demo gif (#7), cost-budget kill-switch (v0.2+, CLAUDE.md hard
  rule #4), `PreToolUse` handler (deferred past v0.1, CLAUDE.md hard rule #5), OTEL export
  (v1.0+, CLAUDE.md hard rule #6).
- **Future Anthropic schema drift** is anticipated and handled (CLAUDE.md hard rule #7). The
  `_on_unknown` recorder writes a generic `event: "unknown"` record carrying the full payload as
  a `raw` subfield, so when Claude Code adds a new event name (or renames an existing one), we
  capture the data instead of crashing. Operators can grep `_self.log` for "non-object payload"
  or "malformed JSON" lines to detect drift early.
