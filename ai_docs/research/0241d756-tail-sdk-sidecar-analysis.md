# Research: `agentlog tail` — SDK sidecar mode

## Metadata

adw_id: `0241d756`
prompt: `/tmp/agentlog_step3_prompt.md` — implement v0.1 ship-scope item #3: `agentlog tail <dir>` ingests `cc_raw_output.jsonl` produced by Claude Code SDK / Anthropic SDK subprocess runs into the same unified `runs/<id>/` directory shape as the hook handlers (ship-scope items #1/#2).
date: `2026-05-27`

## Executive Summary

`tail` is the **scripted-mode** twin of `capture.run_hook`: same on-disk schema (`runs/<id>/{state.json, events.jsonl, cost.json}`), different inputs and different failure contract. Hooks read one payload from stdin per invocation under a fail-open contract; `tail` reads a complete `cc_raw_output.jsonl` (stream-json from `claude -p ... --output-format stream-json --verbose`) under a fail-loud contract. Implementation lands in a new module `src/agentlog/tail.py` plus four argparse args on the existing `tail` subparser in `cli.py`; `_constants.py` grows two values (`SOURCE_SDK`, `EVENT_ASSISTANT_TEXT`); `capture.py` is **not** modified. Risk surface is low — no hot-path budget, stdlib only, deterministic run-id derivation makes idempotency cheap.

## Existing Architecture

### Relevant Documentation Found

| Path | Purpose |
|---|---|
| `DESIGN.md` (lines 87-131) | Data-flow diagram showing both hooks and SDK feeding the unified `runs/<id>/` schema; explicit "Both data sources produce the same directory structure" thesis |
| `DESIGN.md` ship-scope table line 192 | "`agentlog tail <dir>` (SDK sidecar mode) — 1-2 days — Lift JSONL parsing from bbworkflow `adw_modules/agent.py`" |
| `CLAUDE.md` hard rules | Rules #6 (local-first, no network), #7 (`schema_version: 1` always; unknown payloads → log+raw, never crash). Rule #2 (fail-open) applies to hook handlers only — `tail` is fail-loud per prompt. |
| `research/langgraph_patterns_2026.md` | Why ingest-existing-artifacts wins adoption; reinforces the sidecar premise |
| `specs/feature-fabf1d0d-hook-handlers-capture.md` | Spec from the just-landed hooks step — pattern reference for spec-style docs in this repo |
| `ai_docs/research/fabf1d0d-hook-handlers-capture-analysis.md` | Research doc from prior step — format/structure template for this file |

### Component Map

```
            stdin payload                      file on disk
                 │                                  │
                 ▼                                  ▼
        capture.run_hook(event)              tail.run_tail(path, ...)
        (fail-open, returns 0 always)        (fail-loud, rc 0/1/2)
                 │                                  │
                 ├──► dispatch(event, payload)      ├──► walk: find cc_raw_output.jsonl
                 │       │                          ├──► per-file: derive run_id
                 │       └──► _on_<event>(...)      ├──► parse stream-json line by line
                 │              │                   ├──► translate records → events
                 ▼              ▼                   ▼
       ┌──────────────────────────────────────────────────────┐
       │  shared writers: _append_event / _write_state /      │
       │  _write_cost  (currently private to capture.py;      │
       │  duplicate or expose — see "Existing Patterns")      │
       └──────────────────────────────────────────────────────┘
                              │
                              ▼
                  $AGENTLOG_HOME/runs/<id>/
                     ├─ state.json
                     ├─ events.jsonl
                     └─ cost.json
```

### Key Files and Modules

| File | Purpose for this task |
|---|---|
| `src/agentlog/cli.py:11-13` | `SUBCOMMANDS` tuple + `_STUB_SUBCOMMANDS` frozenset; `tail` currently in the stub set and wired to `_not_implemented`. Must drop `"tail"` from `_STUB_SUBCOMMANDS` and add a real subparser branch in the `for name in SUBCOMMANDS` loop (cli.py:24-55) mirroring `init` / `uninstall`. |
| `src/agentlog/cli.py:74-76` | `_not_implemented` — once `tail` ships, remaining stubs are `ls`/`cost`/`view`. Don't touch this. |
| `src/agentlog/capture.py:38-122` | Helpers `_data_root`, `_session_dir`, `_isoformat`, `_truncate`, `_log_self`, `_append_event`, `_write_state`, `_write_cost`, `_read_json` — all module-private. Either expose what's needed (least-surface refactor) or duplicate in `tail.py` (avoids cross-module coupling). Prompt says "use judgment" — see Recommendations. |
| `src/agentlog/capture.py:366-372` | `_DISPATCH` table for hooks-mode events. Prompt is explicit: do **not** modify this; `tail.py` synthesises its own event shape rather than calling `dispatch()`. |
| `src/agentlog/_constants.py:28-34` | Currently exports `SCHEMA_VERSION=1`, `SOURCE_HOOKS="hooks"`, `MAX_INLINE_BYTES=4096`, data-root naming, etc. Must add `SOURCE_SDK = "sdk"` and a constant for the new `"assistant_text"` event kind. |
| `agents/<adw_id>/<phase>/cc_raw_output.jsonl` | Real-world fixtures already in the repo — 5 ADW runs × ~8 phases each. Use `agents/1b4319ab/researcher/cc_raw_output.jsonl` (small, complete) as the primary smoke test target named in the acceptance criteria. |
| `.adw/adw_modules/agent.py:262-341` | Reference implementations of `parse_jsonl_output`, `convert_jsonl_to_json`, `save_last_entry_as_raw_result`. Pattern reference only — **do not import** at runtime per CLAUDE.md code-provenance rules (and per the prompt). The functions there read whole files into memory; `tail` should stream line-by-line per prompt's >10MB edge case. |
| `tests/test_cli_smoke.py:25-32` | Parametrised `test_subcommands_registered_but_not_implemented` covers `c not in {"init", "uninstall"}`. Once `tail` is implemented, **must** update this set to `{"init", "uninstall", "tail"}` or the test will fail when `tail` no longer prints "not yet implemented". |
| `tests/test_capture.py` | Pattern for writing fixture-driven hook tests with `AGENTLOG_HOME` monkeypatched to `tmp_path`. New `tests/test_tail.py` should follow the same shape. |
| `pyproject.toml:28-29` | `dependencies = []` — must stay empty. mypy strict on `src` and `tests`. ruff selects E/W/F/I/B/UP/SIM. |

### Stream-json record shapes (from `agents/1b4319ab/researcher/cc_raw_output.jsonl`)

Empirical record-type counts in a representative file: 1 × `system/init`, 17 × `assistant`, 14 × `user`, 3 × `system/api_retry`, 1 × `rate_limit_event`, 1 × `result/success`.

| Record | Key fields | Translate to |
|---|---|---|
| `{type: "system", subtype: "init"}` | `session_id`, `cwd`, `model`, `tools`, `permissionMode`, `claude_code_version` | `session_start` event + `state.json` (started_at = file mtime of first line OR `now()` per prompt) |
| `{type: "assistant"}` | `message.content` — array of `{type: "thinking"|"text"|"tool_use", ...}` blocks | 0..N events: one `assistant_text` per text block, one `tool_use` per tool_use block. Thinking blocks: ignore (or capture as `assistant_text` with marker — recommend ignore for v0.1, stay minimal). |
| `{type: "user"}` | `message.content` — array; either user text OR `tool_result` blocks. `tool_use_result` sometimes present at top level. | Real user prompts → `prompt` event. Tool-result echoes → skip OR enrich a previous `tool_use` event (prompt says "skip if it's only tool_result echoes" — go with skip in v0.1). |
| `{type: "result", subtype: "success"|"error_*"}` | `usage`, `duration_ms`, `total_cost_usd`, `is_error`, `result`, `num_turns` | `stop` event with usage; update `cost.json`; on `is_error: true` → flag `session_failed: true` in `state.json` |
| `{type: "system", subtype: "api_retry"}` | `attempt`, `error`, `retry_delay_ms` | `event: "unknown"` row (out-of-handler-table; future v0.2 could surface retry visibility) |
| `{type: "rate_limit_event"}` | `rate_limit_info` | `event: "unknown"` row |

Note: every record carries `session_id` at the top level — that's the run-id derivation source. The `system/init` record's `session_id` is canonical, but it's identical across all records in the same file, so the "no init record" fallback path (`sdk-<sha1(abspath)[:12]>`) is genuinely only for truncated/malformed files.

## Affected Areas

### Files That Will Need Changes

| File | Change | Why |
|---|---|---|
| `src/agentlog/tail.py` | **NEW** | Translator + writer + run-id derivation + idempotency check. Public surface: `run_tail(path: Path, *, run_id: str|None, source_name: str|None, dry_run: bool, force: bool) -> int`. |
| `src/agentlog/cli.py` | Modify lines 11-13 (remove `"tail"` from `_STUB_SUBCOMMANDS`) and lines 24-55 (add `tail` branch in the `for name in SUBCOMMANDS` loop with the four flags from the prompt) and add `_run_tail` handler. Add `from agentlog import tail` at line 9. | Wire the new module into the CLI. |
| `src/agentlog/_constants.py` | Add `SOURCE_SDK: str = "sdk"`. Optionally add `EVENT_ASSISTANT_TEXT: str = "assistant_text"` (consistency with how `EVENTS` is centralised) — or hardcode strings in `tail.py` like `capture.py` does. Marginal call; keep `_constants.py` lean unless a value is referenced from ≥2 modules. | New source label per prompt. Constant pinning protects against typos in tests. |
| `src/agentlog/capture.py` | **Possibly** expose `_append_event` / `_write_state` / `_write_cost` / `_data_root` / `_session_dir` / `_truncate` / `_isoformat` / `_log_self` as public names (drop leading underscore or re-export) **or** leave private. See Recommendations for the call. | Re-use vs duplicate the ~30 lines of writer helpers. Prompt says "use judgment". |
| `tests/test_cli_smoke.py:25` | Add `"tail"` to the excluded set: `c not in {"init", "uninstall", "tail"}`. | Otherwise the parametrised "stub" test fails once `tail` is real. |
| `tests/test_tail.py` | **NEW** | Cover acceptance-criteria scenarios: happy path against `agents/1b4319ab/researcher/cc_raw_output.jsonl` fixture (or a synthesised micro-fixture in `tmp_path`), idempotency hit, `--force`, `--dry-run`, missing file (rc=2), empty file, directory walk with N files, multiple-files+`--run-id` error (rc=2), unknown record type → `event: "unknown"`, truncated-mid-stream → `state.truncated=true`. |

### Dependencies

**What `tail.py` depends on:**
- `agentlog._constants` — `SCHEMA_VERSION`, `SOURCE_SDK` (new), `MAX_INLINE_BYTES`, `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SELF_LOG_NAME`
- Stdlib only: `argparse`, `json`, `hashlib` (sha1 fallback id), `os`, `pathlib`, `sys`, `datetime`, `typing`
- Reads from `capture.py`: either direct re-use of helpers (if exposed) or copies of the same 5-line `_append_event` / `_write_state` / `_write_cost` patterns

**What depends on `tail.py`:**
- `cli.py` — single call site `_run_tail`
- Future: `agentlog ls` (#4), `agentlog cost` (#5), `agentlog view` (#6) all read the `runs/<id>/` directory and will see SDK-source runs alongside hooks-source runs — but that's their problem, not `tail`'s, because the schema is unified.

### Integration Points

- **Filesystem only**: same `$AGENTLOG_HOME` resolution as `capture.py` (env var `AGENTLOG_HOME` overrides `~/.agentlog`). Tests must monkeypatch this — established pattern in `tests/test_capture.py:94-98`.
- **No Claude Code coupling**: `tail` does not call `claude`, does not touch `~/.claude/settings.json`, does not fire any hooks. It only reads files the user (or their orchestrator) already wrote.
- **No interaction with `.adw/`**: per CLAUDE.md, `.adw/` is dev-tooling, not a runtime dep. Reference its parsing patterns; do not import from it.

## Impact Analysis

### Scope of Change

**Small.** One new module (~150-250 LOC including the translator), ~30 lines of `cli.py` changes, 2 lines of `_constants.py` additions, one test-file addition (~250 LOC for the coverage matrix), one one-line edit to `test_cli_smoke.py`. No changes to `capture.py` if helpers are duplicated; ~7 trivial renames if helpers are exposed. Zero touch to `hooks_install.py`, `pyproject.toml`, or any installed-file format.

### Risks and Considerations

1. **Schema-shape drift between hooks and SDK records.** The hooks `tool_use` event captures `tool_input` + `tool_response` from the same payload (PostToolUse fires *after* the tool completes). The SDK split is `assistant.tool_use` block (request) → `user.tool_result` block (response) in the next record. Naive translation gives a `tool_use` event with `result_summary: null` and no later enrichment. Two options: (a) ship null result_summary in v0.1 and document it, or (b) buffer pending tool_use ids and back-fill on the matching tool_result. Recommend (a) for v0.1 simplicity — the prompt explicitly says "no result yet — result comes from the subsequent user/tool_result record" but then doesn't require back-fill. Document this in the spec.

2. **`assistant_text` is a new event kind.** Hooks mode never emits it (Anthropic doesn't fire hooks on assistant text turns). Downstream readers (`view`, `cost`) must learn it — but they're separate ship-scope items, so out of scope here. Just make sure `_constants.py` documents the kind so #4/#5/#6 pick it up.

3. **`_DISPATCH` invariant.** `capture.py` has a comment (line 374-378) noting that `set(_DISPATCH) == set(EVENTS)` is enforced by a test (`test_dispatch_table_matches_events`) rather than an assert. The new SDK event types don't go through `_DISPATCH`, so they don't break this invariant — but `EVENTS` is still hook-specific. Don't add `"AssistantText"` to `EVENTS`.

4. **Fail-loud vs fail-open is inverted.** Easy to get wrong by reflex. Hook code is full of `except Exception` swallows; `tail` must let exceptions surface (or convert to clean stderr + rc≠0). Lift the writer helpers carefully — `_log_self` swallows its own errors, which is fine for `tail` too (logging best-effort), but the main path must propagate.

5. **`--run-id` semantics.** Prompt edge-case section L96 resolves to: user-supplied `--run-id` is stored **verbatim** (no auto-prefix), and only auto-derived ids get the `sdk-` prefix. This is non-obvious; tests must lock it.

6. **Streaming large files.** Use `with open(...) as f: for line in f:` (line-buffered iteration), not `f.read()`. The `.adw/adw_modules/agent.py::parse_jsonl_output` reference reads the whole file — explicitly do not copy that pattern.

7. **Atomic writes for state.json/cost.json.** `capture.py` uses `os.replace(tmp, path)` (lines 95-98, 104-108). `tail.py` must do the same; otherwise a crash mid-`tail` leaves a partial JSON file that breaks the next read. `events.jsonl` is append-only and tolerates truncated last-line.

8. **Idempotency with `--force` and partial writes.** Prompt says `--force` blows away existing run-dir contents then re-writes. Implementation: rmtree-then-mkdir, or unlink the three known files. Prefer unlink-the-three so we never accidentally remove `_logs/` content from a future feature.

9. **mypy strict.** `tests/test_cli_smoke.py` and `tests/test_capture.py` show the project takes the strict-typing rules seriously (explicit `Any`/`dict[str, Any]`/`pytest.MonkeyPatch` annotations). `tail.py` and `tests/test_tail.py` need the same discipline; a generic `dict` will fail CI.

10. **ruff B (bugbear) and SIM** are on. Watch for `B008` (mutable default args) and `SIM` (collapse `if x: return True else: return False`). Existing modules pass cleanly — match the style.

### Existing Patterns to Follow

- **Module docstring with rule citations** — see `capture.py` lines 1-9 and `_constants.py` lines 1-12. Cite CLAUDE.md rules that the module pins.
- **Public surface at the bottom of the module, helpers above** — `capture.py` ends with `dispatch()` and `run_hook()`. `tail.py` should mirror: helpers first, `run_tail()` at the bottom.
- **`from __future__ import annotations`** at top of every module. Project is 3.11+ but the import is uniformly applied.
- **Use `from agentlog._constants import …`** rather than `from agentlog import _constants` — see `capture.py:23-31`.
- **Test fixtures via `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))`** — see `tests/test_capture.py:94-99`.
- **Subparsers register inline in the `for name in SUBCOMMANDS` loop** — see `cli.py:24-55`. Don't extract a `_build_tail_parser` helper; the existing style is inline `elif name == "tail":` to match `init`/`uninstall`.
- **Schema-version every record** — `SCHEMA_VERSION = 1` in every JSONL row and every state/cost file. Bumping requires migration plan per `_constants.py:8-11`.
- **CLI exit codes**: `0` happy, `2` for argparse-shaped errors (matches `_not_implemented`). Reserve `1` for runtime IO failures (file-system errors etc.). Prompt says rc=2 for "missing file" / "multiple files + --run-id" — match that.

## Recommendations

### Helper re-use vs duplicate

**Recommend duplicate, not expose.** `capture.py`'s helpers are private (`_append_event`, `_write_state`, `_write_cost`, `_log_self`, `_truncate`, `_isoformat`, `_data_root`, `_session_dir`). Exposing them turns them into API surface and couples `tail.py`'s evolution to `capture.py`'s. The helpers are 30 lines total. Copy them into `tail.py` (or a tiny `_io.py` shared module if the duplication itches). Rationale: capture and tail have different failure contracts (fail-open vs fail-loud); divergent error handling will accumulate over time, and a shared helper module forces premature abstraction. Mirror the same code; let it drift consciously if the contracts diverge.

If duplication still feels wrong, the second-best option is a new `src/agentlog/_io.py` exporting `data_root`, `session_dir`, `append_event`, `write_state`, `write_cost`, `truncate`, `isoformat`, `log_self` (public names) imported by **both** `capture.py` and `tail.py`. This is a follow-up refactor that should not be bundled with this feature unless it's trivial — the prompt explicitly says "or duplicate … if importing pulls in too much surface".

### Translator design

Single-pass over the JSONL file. Emit `session_start` from the first `system/init`, then iterate. Maintain minimal per-file state: `session_id`, `started_at`, `model`, `cwd`, `truncated: bool`, `session_failed: bool`. Tool-use back-fill is deferred (point 1 under Risks).

```python
def _translate(records: Iterable[dict], abs_path: Path) -> Iterator[dict]:
    # Yield event dicts ready for _append_event. Caller does the I/O.
    ...
```

Keep `_translate` pure (no I/O, no clock). Caller injects `now()` and `abs_path` so tests can pass fixed values.

### Run-id derivation

```python
def derive_run_id(path: Path, explicit: str | None) -> tuple[str, bool]:
    """Return (run_id, derived_from_fallback).

    explicit  → returned verbatim (no sdk- prefix). Caller ensures single-file mode.
    no init   → 'sdk-' + sha1(str(path.resolve()))[:12], flag True
    init      → 'sdk-' + session_id, flag False
    """
```

Single-purpose, easy to unit-test without writing a file.

### Test fixtures

Two strategies, complementary:

1. **Hand-written micro-JSONL** in `tests/fixtures/sdk_minimal.jsonl` (10-20 lines covering: init, one assistant text, one tool_use+tool_result pair, one user prompt, one result). Tiny and stable.
2. **One real fixture** copied from `agents/1b4319ab/researcher/cc_raw_output.jsonl` (~1KB-ish) to lock the "real file works" acceptance criterion. Or just point the test at the in-tree path — but that risks the file being deleted in a future cleanup. Copy is safer.

### Implementation order

1. `_constants.py` — add `SOURCE_SDK` (1 line).
2. `tail.py` scaffold — helpers, translator, `run_tail()` happy path against a fixture.
3. `cli.py` wiring — drop `tail` from `_STUB_SUBCOMMANDS`, add subparser + `_run_tail`.
4. `tests/test_cli_smoke.py` — fix the parametrize exclusion.
5. `tests/test_tail.py` — happy path test first to anchor the schema, then edge cases.
6. mypy strict + ruff pass — fix.
7. Manual smoke against `agents/1b4319ab/researcher/cc_raw_output.jsonl` per acceptance criteria.

### Out-of-scope reminders (do not let them creep in)

- No live `tail -f` mode.
- No multi-source merge (one hook session + one SDK file into one run dir).
- No `agentlog ls` / `cost` / `view` updates — those are ship-scope items #4/#5/#6.
- No `agentlog.subprocess(...)` Python wrapper (v0.2+).
- No README update (docs phase produces `docs/feature-0241d756-tail-sdk-sidecar.md`).
- Do **not** add `tail` deps to `pyproject.toml`; runtime stays stdlib-only.
