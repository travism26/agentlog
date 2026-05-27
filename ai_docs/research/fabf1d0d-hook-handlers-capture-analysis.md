# Research: Hook Handler Bodies (`agentlog _hook <Event>` capture logic)

## Metadata

adw_id: `fabf1d0d`
prompt: `/tmp/agentlog_step2_prompt.md` — implement v0.1 ship-scope item #2: the real hook handler bodies behind `agentlog _hook <Event>` that replace today's `lambda args: 0` no-op and write the unified `runs/<id>/` schema.
date: `2026-05-27`

## Executive Summary

Ship-scope item #1 (commit `390bc7b`) already lands a hidden `_hook` subparser in `src/agentlog/cli.py:59-64` that argparses an `event` positional and routes to a no-op `lambda args: 0`. The remaining work is contained: add ONE new stdlib-only module (`src/agentlog/capture.py`, per the DESIGN.md "Code lifted from bbworkflow" table at line 250) exporting `dispatch(event, payload, *, now=None)` and `run_hook(event)`; replace the lambda in `cli.py` with a real `_run_hook` that calls `capture.run_hook(args.event)`. The hot-path budget (<10ms steady, <50ms cold) and fail-open contract (CLAUDE.md rule #2) make `run_hook` the only place that may touch `sys.stdin` or `os.environ` — every line in it sits inside a top-level `try/except Exception` that logs to `~/.agentlog/_self.log` and returns 0. No new runtime deps; `pyproject.toml:28` `dependencies = []` stays empty.

## Existing Architecture

### Relevant Documentation Found

- **`DESIGN.md`** (root) — locked v0.1 design.
  - "Hook integration" table (lines 137-145) is the per-event behavior contract (`SessionStart` → create dir + metadata; `UserPromptSubmit` → append prompt event; `PostToolUse` → append tool call + result; `Stop` → flush usage; `SessionEnd` → finalise state).
  - "Performance contract" table (lines 149-159): <50ms cold, <10ms steady, JSONL write <2ms, network = FORBIDDEN. Plus the implementation rules (exit 0 ALWAYS, all analysis deferred to read-time, self-errors → `~/.agentlog/_self.log`).
  - "Unified `runs/<id>/` schema" (lines 109-131) — the directory shape this task must produce: `runs/<session_id>/{state.json,events.jsonl,cost.json,_logs/}`. Run-id is Claude Code's `session_id` for hook mode; `parent_session_id` lives in `state.json` for subagent fleet view.
  - "Risks" row 6 (line 227): on Anthropic hook-payload schema drift, version (`schema_version: 1`), log warning, write raw payload anyway — do not crash.
  - "Code lifted from bbworkflow (sanitized)" table (lines 247-252): the JSONL capture pattern from `adw_modules/agent.py` is destined for `agentlog/capture.py` — picking `capture.py` over `handlers.py` matches the locked plan.
- **`CLAUDE.md`** (root) — hard rules.
  - #1 hot-path budget (no network, no heavy imports).
  - #2 fail-open ALWAYS — load-bearing for the `try/except` shape of `run_hook`.
  - #4 no cost-budget kill-switch in v0.1.
  - #5 no `PreToolUse` handler (already enforced upstream by `hooks_install.EVENTS`).
  - #7 schema versioned (`schema_version: 1`) and tolerate Anthropic schema drift via `event: "unknown"` records.
- **`ai_docs/research/1b4319ab-init-uninstall-cli-analysis.md`** — predecessor research for item #1; relevant section is the forward-compatibility note (item #1 recommendation #4) that flagged the hidden `_hook` subparser as the integration seam for this task.
- **`specs/feature-1b4319ab-init-uninstall-hooks.md`** and **`docs/feature-1b4319ab-init-uninstall-hooks.md`** — confirms the `HOOK_COMMAND_PREFIX = "agentlog _hook"` sentinel is part of the installed-file format and must not change.
- **`research/langgraph_patterns_2026.md`** and **`research/ai_dev_pain_points_2026.md`** — background framing only; not directly relevant to handler internals.

### Component Map

```
src/agentlog/
├── __init__.py            # __version__ = "0.1.0.dev0"   (unchanged)
├── __main__.py            # `python -m agentlog` → cli.main()   (unchanged)
├── cli.py                 # MODIFIED: replace `lambda args: 0` at line 61 with `_run_hook`
├── hooks_install.py       # UNCHANGED — HOOK_COMMAND_PREFIX, EVENTS, plan_install/uninstall, write_atomic
└── (NEW) capture.py       # dispatch(event,payload,*,now=None) + run_hook(event) + per-event recorders

tests/
├── __init__.py
├── test_cli_smoke.py      # unchanged unless we add a smoke for `_hook` (already covered by
│                          # test_hooks_install.py::test_hook_noop_subparser_exits_zero — that test
│                          # will keep passing because run_hook returns 0 on empty stdin)
├── test_hooks_install.py  # unchanged
└── (NEW) test_capture.py  # unit tests for dispatch + per-event recorders + run_hook + fail-open
└── (NEW, optional) test_handler_perf.py  # asserts <10/<50ms budgets (tech_debt-class)

~/.agentlog/                            # NEW data root (env-overridable via AGENTLOG_HOME)
├── _self.log                           # fail-open landing zone (line-appended)
└── runs/
    └── <session_id>/
        ├── state.json
        ├── events.jsonl                # append-only, JSONL with schema_version: 1
        ├── cost.json
        └── _logs/                      # reserved (empty in v0.1)
```

### Key Files and Modules

| Path | Purpose for this task |
|------|----------------------|
| `src/agentlog/cli.py:59-64` | The seam. Replace the `lambda args: 0` on line 61 with `func=_run_hook`, and add `def _run_hook(args)` that calls `capture.run_hook(args.event)`. Keep the `_choices_actions` filter that hides `_hook` from `--help`. Do NOT widen `SUBCOMMANDS` (it's iterated by the smoke test). |
| `src/agentlog/hooks_install.py:34-42` | Read-only here. `EVENTS = ("SessionStart","UserPromptSubmit","PostToolUse","Stop","SessionEnd")` and `HOOK_COMMAND_PREFIX = "agentlog _hook"` define the events that will actually fire. New module imports `EVENTS` for routing if convenient (or duplicates the tuple to keep `capture.py` standalone-importable in the hot path — see "Risks" #2). |
| `src/agentlog/capture.py` (NEW) | Implementation lives here. Public surface: `dispatch`, `run_hook`, plus the per-event recorders. Internal helpers: `_resolve_data_root()`, `_session_dir(session_id)`, `_append_event(dir, record)`, `_log_self(msg)`, `_truncate(payload_blob, limit)`. |
| `tests/test_capture.py` (NEW) | Coverage. Must include: each per-event recorder, malformed stdin, empty stdin, unknown event, missing session_id, read-only data root (fail-open), large payload truncation, concurrent appends within `PIPE_BUF`, `SessionEnd`-before-`SessionStart` ordering, `schema_version` presence on every record. |
| `DESIGN.md:109-131,137-159` | Schema and performance contract to satisfy. |
| `CLAUDE.md` hard rules #1, #2, #5, #7 | Acceptance gates. |
| `pyproject.toml:28` | `dependencies = []` MUST remain empty after this change. mypy strict, ruff with `I/B/UP/SIM` are configured project-wide; new code must pass. |
| `tests/test_hooks_install.py:430-438` | Existing `test_hook_noop_subparser_exits_zero` test. Will still pass — `run_hook("SessionStart")` against no stdin must return 0 (fail-open). |

## Affected Areas

### Files That Will Need Changes

| File | Change |
|------|--------|
| `src/agentlog/cli.py` | (a) Add `from agentlog import capture` (or import lazily inside `_run_hook` to keep cold-start fast — see Risks). (b) Replace `hook_sp.set_defaults(func=lambda args: 0)` on line 61 with `hook_sp.set_defaults(func=_run_hook)`. (c) Add `def _run_hook(args: argparse.Namespace) -> int: return capture.run_hook(args.event)`. |
| `src/agentlog/capture.py` (NEW) | Pure-stdlib module: `dispatch(event,payload,*,now=None) -> int`, `run_hook(event) -> int`, per-event recorders (`_on_session_start`, `_on_user_prompt_submit`, `_on_post_tool_use`, `_on_stop`, `_on_session_end`, `_on_unknown`), and helpers (`_data_root`, `_session_dir`, `_append_event`, `_log_self`, `_truncate`). Top-level `try/except Exception` in `run_hook`. |
| `tests/test_capture.py` (NEW) | Dedicated coverage. ~15-20 tests covering happy paths, fail-open paths, schema drift, truncation, ordering. |
| `tests/test_handler_perf.py` (NEW, optional) | Budget assertions via `pytest --durations=10` or explicit `time.perf_counter` deltas. Treat budget failures as `tech_debt` per the test slash-command's hard rules (acceptance criterion: "non-blocking failure"). |
| `pyproject.toml` | NO change. `dependencies = []` stays. |
| `tests/test_cli_smoke.py` | NO change. Already excludes `init`/`uninstall` from the "not implemented" parametrize; `_hook` was never in `SUBCOMMANDS` so it isn't checked here. |

### Dependencies

- **What this code depends on**:
  - stdlib only: `json`, `os`, `sys`, `time`, `pathlib.Path`, `datetime.datetime`/`timezone`, `typing` annotations. The prompt is explicit about the allowed import set ("`json`, `os`, `pathlib`, `datetime`, `sys`, `time` — that set").
  - `agentlog.hooks_install.EVENTS` (optional — see Risks #2).
- **What depends on this code**:
  - Item #4 `agentlog ls` will read `state.json` across all `runs/<id>/` directories and (probably) maintain a SQLite index.
  - Item #5 `agentlog cost <id>` will read `cost.json` (and possibly re-derive from `events.jsonl` if `cost.json` is partial).
  - Item #6 `agentlog view <id>` will stream `events.jsonl` in order. The order, JSONL one-record-per-line contract, and `schema_version: 1` field are the wire format these consumers depend on.
  - Item #3 `agentlog tail <dir>` (SDK sidecar) will write into the SAME `runs/<id>/` schema, so the JSONL record shape decided here is locked-in for SDK mode too. Choose field names with that future use in mind (e.g., `source: "hooks" | "sdk"` on every record).

### Integration Points

1. **Claude Code hook payload contract.** Anthropic's hooks pass a JSON object on stdin to the registered command. Every payload includes `session_id`; per the prompt and DESIGN.md, payloads also carry event-specific fields (prompt text on `UserPromptSubmit`, tool name + params + result on `PostToolUse`, `usage` block on `Stop`). Schema drift is anticipated (CLAUDE.md #7): write an `event: "unknown"` record with the raw payload rather than crash. **Open question**: do we have the exact 2026-05-27 payload field names from Anthropic's docs, or must we treat the payload as opaque except for `session_id`? Recommend: treat opaque except `session_id` and any field the per-event recorder explicitly extracts; copy the whole payload into a `raw: {...}` subfield so downstream tools can mine it later without us guessing today.
2. **Filesystem `~/.agentlog/`.** Created lazily on first `SessionStart`. `AGENTLOG_HOME` env override per prompt. POSIX append-mode atomicity for `<PIPE_BUF` (~4KB on Linux/macOS) is the documented concurrency guarantee — no flock, no SQLite, no temp-file dance for `events.jsonl`. For `state.json`/`cost.json` (whole-file rewrites), use the existing `hooks_install.write_atomic` pattern (temp + `os.replace`) — but think about whether we need to import it or duplicate (Risks #2).
3. **Existing `agentlog init`-installed `settings.json`.** The hook entries written there invoke `agentlog _hook <Event>`. `agentlog` is the installed console_script (`pyproject.toml:54-55`). On a developer machine without `agentlog` on PATH, the hooks 127-error and Claude Code silently swallows it. Not our problem at the handler layer — but the documentation we ship later must call this out.
4. **`hooks_install.EVENTS`.** The single source of truth for which events are registered. The new module's dispatch table should iterate or reference this so adding/removing an event is a one-line change in one file. The trade-off (Risks #2): importing from `hooks_install` pulls `copy`, `difflib`, etc. into the hot path. Mitigation: extract `EVENTS` (and `HOOK_COMMAND_PREFIX` if we want to canonicalize sentinels) into a tiny `_constants.py` that both `hooks_install` and `capture` import from. Or just duplicate the 5-element tuple with a comment.

## Impact Analysis

### Scope of Change

Small and well-bounded: 1 new module (~250-350 LOC counting docstrings and per-event recorders), 1 new test file (~300-400 LOC for 15-20 tests), one ~5-line edit in `cli.py`. No changes to packaging, no changes to existing tests, no new runtime deps. Risk surface is concentrated in two places: (a) the fail-open boundary in `run_hook` (must catch literally everything except `SystemExit`/`KeyboardInterrupt`) and (b) the on-disk JSONL schema, which is the wire format three downstream features depend on.

### Risks and Considerations

1. **Fail-open completeness.** A `BaseException` (e.g., `MemoryError`, `KeyboardInterrupt`) in a handler will still propagate out of `except Exception`. The right balance for a Claude Code hot path: catch `Exception` only — let `KeyboardInterrupt` and `SystemExit` through so Ctrl+C in Claude Code still works. But: any `OSError` writing to `_self.log` itself can re-raise. Wrap the `_log_self` body in its own `try/except Exception: pass`. Tests must cover the "even the self-log write fails" path — verify the process still exits 0.
2. **Cold-start budget vs `hooks_install` reuse.** Importing `hooks_install` from `capture.py` pulls in `copy`, `difflib`, and the rest of that module just to read a 5-tuple. Two options: (a) duplicate `EVENTS` in `capture.py` with a comment pointing back to `hooks_install`; (b) extract a `src/agentlog/_constants.py` with `EVENTS` and `HOOK_COMMAND_PREFIX`. **Recommend (b)** — keeps a single source of truth and adds an empty-module import cost which is negligible. Then `cli.py:9` becomes `from agentlog import __version__, hooks_install, capture` (or lazily import `capture` inside `_run_hook` if benchmarks show import cost dominates cold-start).
3. **`session_id` missing or malformed.** If Anthropic's payload changes and `session_id` becomes nested under `session.id` or similar, the recorder will write under `runs/unknown_session/` (prompt's fallback). That collides for concurrent unknown sessions, so make the fallback `unknown_session-<pid>-<unix_ms>` to avoid clobbering. Log a `_self.log` warning the first time it happens so we notice schema drift.
4. **Large prompts/tool outputs.** A 10MB tool result inlined into `events.jsonl` blows up `events.jsonl` and slows every downstream reader. Truncate at 64KB (configurable via constant) and record `truncated_bytes: N`. Critical detail: truncation must NOT happen in the hot path's serializer — `json.dumps` of a 10MB string is itself slow. Truncate the raw blob BEFORE `json.dumps`. Bench this.
5. **POSIX append atomicity boundary.** `<PIPE_BUF` (4096 on Linux/macOS) per `man 2 write`. A single JSONL record can exceed that — especially with `raw: {full_payload}` plus a 64KB truncation ceiling on prompt text. Three options: (a) hard-cap each record at 4KB (lossy); (b) use a flock; (c) accept that two simultaneous handler processes can produce interleaved records on rare occasions and document it. The prompt picks (c) ("Document this; don't add locking"). Make sure the record-shape decision keeps the COMMON case under 4KB — i.e., don't inline a 64KB prompt with the truncation limit applied; truncate to something like 4KB for the inlined `text` and store full prompts in a separate sidecar if we ever decide it matters. **Recommend**: inline up to 4KB; record `truncated_bytes: N` if longer; defer "full prompt" capture to a future v0.2 feature.
6. **`SessionEnd` before `SessionStart`.** Edge case from the prompt. `_on_session_end` and `_on_user_prompt_submit` and the others must all create the run dir lazily (`mkdir parents=True, exist_ok=True`) rather than assume `_on_session_start` has run. Treat `_on_session_start` as "writes the initial state.json" rather than "first to create the dir." Tests must include `_on_session_end` against a tmp_path that never saw `_on_session_start`.
7. **State-file writes and concurrency.** `state.json` and `cost.json` are whole-file rewrites. Two concurrent `SessionEnd`s for different sessions are fine (different dirs); two concurrent `Stop` events for the SAME session (subagent races) could clobber. Mitigation: use the existing `write_atomic` pattern (temp file + `os.replace`) — `os.replace` is atomic on POSIX, so the worst case is "last writer wins" rather than corruption. Document the trade-off; do not add locking.
8. **`schema_version` placement.** Must be on every record per CLAUDE.md #7. Don't bury it inside a sub-object. Put it at the top level of each JSONL line and each JSON file. Pick `1` (integer) — easier to compare than a string.
9. **Datetime injection for tests.** Take `now: datetime | None = None` on `dispatch` (and pass through to recorders) per the prompt. Default to `datetime.now(timezone.utc)`. Always serialize as ISO-8601 with `+00:00` so timestamps sort lexically. Do NOT use `datetime.utcnow()` (deprecation-track in 3.12+).
10. **`os.getenv("AGENTLOG_HOME")` caching.** Tests will monkeypatch the env var per-test; do NOT cache the resolved path at module import time. Resolve inside `_data_root()` on every call. The cost is one `os.environ.get` per handler invocation — fast.
11. **Hidden `_hook` subparser stays hidden.** `cli.py:64`'s `_choices_actions` filter keeps `_hook` out of `--help`. That line must survive the refactor — it's a fragile internal of argparse. If we break it, `agentlog --help` will start advertising `_hook` to users.
12. **`run_hook` signature on partial install.** If a user has an old `settings.json` written by an even-earlier version with a different command shape, our `_hook` subparser still gets called with `event` as a positional. We only need to support the `agentlog _hook <Event>` form `init` writes today. Older installs aren't a thing in v0.1.

### Existing Patterns to Follow

- **`from __future__ import annotations` + explicit return types** — used in `cli.py`, `hooks_install.py`, all tests. Apply to new code; mypy strict will demand it (`pyproject.toml:80-83`).
- **`pyproject.toml` ruff rules** — `E/W/F/I/B/UP/SIM`. Avoid `try: ... except Exception: pass` bare patterns without a noqa comment; the ruff `BLE001` rule isn't in the select set, but `B` family does flag some bare-except patterns. Use `except Exception as e: ...` and capture.
- **Test style** — `pytest` with `tmp_path` for filesystem isolation, `monkeypatch.setenv` for `HOME`/`AGENTLOG_HOME` overrides, `capsys` for stdout/stderr capture. Never touch real `~/.agentlog/`. Use `parametrize` for table-driven event cases.
- **Pure-functions-first layering** — `hooks_install.py` (lines 67-129) splits pure plan functions from filesystem helpers from CLI orchestrators. Follow the same layering in `capture.py`: pure recorders that take a dict + tmp dir and return a dict, with a thin orchestration layer on top that does the I/O. Makes tests cleaner.
- **Stdlib-only** — `pyproject.toml:28`. No new deps for this task. No `pydantic`, no `rich`, no `dotenv` — even though `.adw/adw_modules/agent.py` uses them, that subtree is dev tooling, not product code.

## Recommendations

### Module surface for `src/agentlog/capture.py`

```python
# Constants
SCHEMA_VERSION: int = 1
SOURCE_HOOKS: str = "hooks"
DEFAULT_DATA_ROOT: Path = Path.home() / ".agentlog"
SELF_LOG_NAME: str = "_self.log"
RUNS_DIR_NAME: str = "runs"
UNKNOWN_SESSION_PREFIX: str = "unknown_session"
MAX_INLINE_BYTES: int = 4096        # keep individual records < PIPE_BUF
TRUNCATION_FIELD: str = "truncated_bytes"

# Public surface
def dispatch(event: str, payload: dict, *, now: datetime | None = None) -> int: ...
def run_hook(event: str) -> int: ...

# Per-event recorders (private, called by dispatch)
def _on_session_start(payload, now, root) -> None: ...
def _on_user_prompt_submit(payload, now, root) -> None: ...
def _on_post_tool_use(payload, now, root) -> None: ...
def _on_stop(payload, now, root) -> None: ...
def _on_session_end(payload, now, root) -> None: ...
def _on_unknown(event, payload, now, root) -> None: ...

# Helpers
def _data_root() -> Path: ...                                   # honors AGENTLOG_HOME
def _session_dir(root: Path, session_id: str) -> Path: ...
def _append_event(session_dir: Path, record: dict) -> None: ... # opens 'a', writes one line
def _write_state(session_dir: Path, state: dict) -> None: ...   # atomic via temp+os.replace
def _write_cost(session_dir: Path, cost: dict) -> None: ...     # same
def _read_json(path: Path) -> dict: ...                         # returns {} on missing/malformed
def _log_self(root: Path, message: str) -> None: ...            # appends to _self.log; swallows errors
def _truncate(value: str, limit: int) -> tuple[str, int]: ...   # returns (clipped, dropped_bytes)
def _isoformat(dt: datetime) -> str: ...                        # always +00:00 UTC
def _fallback_session_id() -> str: ...                          # unknown_session-<pid>-<unix_ms>
```

### Event-record shapes (decision in this task — locks the wire format)

Every record on `events.jsonl` carries: `schema_version: 1`, `event: <name>`, `timestamp: <iso>`, `session_id: <id>`, `source: "hooks"`. Plus event-specific fields:

```jsonc
// SessionStart
{"schema_version":1,"event":"session_start","timestamp":"...","session_id":"...","source":"hooks",
 "parent_session_id":null,"cwd":"...","model":"..."}

// UserPromptSubmit
{"schema_version":1,"event":"prompt","timestamp":"...","session_id":"...","source":"hooks",
 "text":"...","text_bytes":1234,"truncated_bytes":0}

// PostToolUse
{"schema_version":1,"event":"tool_use","timestamp":"...","session_id":"...","source":"hooks",
 "tool":"Bash","params_summary":"...","result_summary":"...","duration_ms":null,"truncated_bytes":0}

// Stop
{"schema_version":1,"event":"stop","timestamp":"...","session_id":"...","source":"hooks",
 "usage":{"input_tokens":...,"output_tokens":...,"cache_read_tokens":...,"cache_creation_tokens":...}}

// SessionEnd
{"schema_version":1,"event":"session_end","timestamp":"...","session_id":"...","source":"hooks",
 "summary":null}

// Unknown / schema drift
{"schema_version":1,"event":"unknown","timestamp":"...","session_id":"...","source":"hooks",
 "original_event":"WhateverNewName","raw":{...full payload...}}
```

`state.json` shape (whole-file, last-writer-wins):

```jsonc
{"schema_version":1,"session_id":"...","parent_session_id":null,
 "started_at":"...","ended_at":null,"cwd":"...","model":"...",
 "event_count":0,"source":"hooks","summary":null}
```

`cost.json` shape:

```jsonc
{"schema_version":1,"session_id":"...",
 "totals":{"input_tokens":0,"output_tokens":0,"cache_read_tokens":0,"cache_creation_tokens":0},
 "phases":{}}   // phases is reserved; item #5 fills it in
```

### `run_hook` shape (the fail-open boundary)

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
    except Exception as exc:  # noqa: BLE001 - fail-open per CLAUDE.md rule #2
        try:
            _log_self(root, f"unhandled in {event}: {exc!r}")
        except Exception:
            pass
        return 0
```

### CLI wiring in `cli.py`

```python
# at top
from agentlog import __version__, capture, hooks_install

# replace the lambda at line 61 with:
hook_sp.set_defaults(func=_run_hook)

# add:
def _run_hook(args: argparse.Namespace) -> int:
    return capture.run_hook(args.event)
```

Keep the `sub._choices_actions = [...]` filter on line 64 intact — that's how `_hook` stays out of `--help`.

### Test plan (matches prompt acceptance criteria)

- `test_dispatch_session_start_writes_state_and_event` — happy path; `state.json` exists, `events.jsonl` has 1 record, every field schema-conformant.
- `test_dispatch_user_prompt_submit_appends_prompt_event` — prompt text captured, `text_bytes` matches.
- `test_dispatch_post_tool_use_records_tool_and_summaries` — params and result summaries truncated to `MAX_INLINE_BYTES`.
- `test_dispatch_stop_updates_cost_json` — `cost.json.totals` populated from `payload["usage"]`.
- `test_dispatch_session_end_finalises_state` — `ended_at` set, `event_count` reflects events.jsonl line count.
- `test_dispatch_unknown_event_writes_generic_record` — `event: "unknown"`, `original_event` carries Anthropic's name, `raw` carries the whole payload, exit 0.
- `test_run_hook_malformed_stdin_logs_and_returns_zero` — `_self.log` grew, no `runs/` dir created.
- `test_run_hook_empty_stdin_returns_zero` — same, plus distinct `_self.log` line.
- `test_run_hook_missing_session_id_uses_fallback` — record lands under `runs/unknown_session-<…>/`.
- `test_run_hook_read_only_root_returns_zero` — chmod 0o500 on `~/.agentlog/`, dispatch a SessionStart, assert rc=0 and no exception.
- `test_session_end_before_session_start_tolerated` — only `_on_session_end` called; run dir created lazily; no crash.
- `test_truncation_records_truncated_bytes_field` — feed >MAX_INLINE_BYTES prompt; field set, content clipped.
- `test_schema_version_is_one_on_every_record` — across all event types.
- `test_agentlog_home_env_var_redirects_root` — `monkeypatch.setenv("AGENTLOG_HOME", str(tmp))`; assert files land under tmp.
- `test_hook_noop_subparser_exits_zero` — already exists in `test_hooks_install.py:430`; must continue to pass.
- `test_run_hook_unknown_event_with_no_session_id` — combine two edge cases.
- (optional) `test_handler_perf.py::test_cold_start_under_50ms` and `test_steady_state_under_10ms` — `time.perf_counter` deltas around `run_hook`. Per the prompt, treat budget failures as `tech_debt` (non-blocking).

### What NOT to do

- Do NOT change `HOOK_COMMAND_PREFIX` or `EVENTS` in `hooks_install.py` — that string is part of the installed-file format (`hooks_install.py:6-21` head docstring). If extracting `EVENTS` into a shared `_constants.py`, re-export from `hooks_install.py` so external imports still work.
- Do NOT cache `_data_root()` at module-import time — tests monkeypatch the env var per-test.
- Do NOT import `rich`, `pydantic`, `dotenv`, or anything else outside the prompt-allowed stdlib set in the hot path.
- Do NOT touch `~/.agentlog/` from any test — use `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))`.
- Do NOT add flocking, SQLite, or any concurrency primitive — POSIX append atomicity + `os.replace` is the design.
- Do NOT add a `PreToolUse` recorder, even as a stub. CLAUDE.md rule #5.
- Do NOT add a network call, telemetry ping, or version check — anywhere in the handler hot path. CLAUDE.md rule #1, #6.
- Do NOT update `pyproject.toml`'s `dependencies` array — must stay `[]`.
- Do NOT amend the previous commit or use `--no-verify`; create a new commit with the new module + tests + cli edit.
- Do NOT touch the README in this task; that lives in ship-scope item #7 and the docs slash command will generate `docs/feature-fabf1d0d-hook-handlers.md` separately.
