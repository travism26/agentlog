# Feature: `agentlog view <id>` — three-panel TUI hero artifact

## Metadata

adw_id: `cb153ac3`
prompt: `/tmp/agentlog_step6_prompt.md` — Implement v0.1 ship-scope item #6 from DESIGN.md: `agentlog view <id>`, the static three-panel `rich`-based renderer for one captured run (header card + chronological timeline + cost footer). This is the README hero screenshot; visual quality matters more than feature breadth. Adds module `src/agentlog/view.py`, pulls `"view"` out of `_STUB_SUBCOMMANDS` in `cli.py`, gates the `rich` import inside `run_view` so `--json` works without it, no new runtime deps.

## Feature Description

`agentlog view` is the inspect-one-run surface for v0.1: given a `run_id` produced by `agentlog ls`, it reads `runs/<id>/state.json`, `events.jsonl`, and `cost.json` and renders a single static screen in three vertical sections:

1. **Header panel** — `rich.box.HEAVY` framed card. One fact per row: `Source`, `Model`, `Cwd`, `Started`, `Duration`, `Events`, `Cost`. Right-aligns the second column on the paired rows so `Duration:` aligns with `Cost:`. Title is the run id.
2. **Timeline section** — bordered with angle-bracket left rail (`┌`/`│`/`└`). One row per event, sorted by `timestamp` ascending (oldest at top). Each row: `<HH:MM:SSZ>  <event_kind padded>  <per-kind summary>`. Event kinds color-coded (session_start/session_end bold-magenta, prompt cyan, assistant_text green, tool_use yellow, stop bold-blue, unknown dim).
3. **Cost footer** — same four-row breakdown as `agentlog cost <id>` plain output (input/output/cache_read/cache_creation × tokens × rate × cost), totalled. No preamble, just the table.

The `tool_use` row is the load-bearing case for use-case #2 ("did the agent actually do what it claimed"). Each tool_use renders as `tool_use  Read    src/agentlog/cli.py` — i.e., tool name (left-padded to ~8 chars) plus a per-tool summary extracted from `params_summary` (`file_path` for Read/Edit/Write, `pattern in path` for Grep, first 60 chars of `command` for Bash, `pattern` for Glob, raw truncated fallback otherwise). Per-tool extraction is dispatch-table-driven.

Flags:

- `--limit N` (default 100; 0 = unlimited) — cap timeline events shown. When capped, append `… (N more events; use --limit 0 to see all)`.
- `--events-only` — skip header panel and cost footer, render only the timeline.
- `--no-truncate` — disable the 80/60-char per-row display cap. (The on-disk `MAX_INLINE_BYTES` cap still applies at write time.)
- `--json` — bypass `rich` entirely; emit one combined JSON object `{run_id, state, cost: {totals, computed, pricing_source}, events: [...]}`.

Rich is gated through the existing `[tui]` extra. Importing `rich.*` happens INSIDE `run_view`, after the `--json` branch returns; `view --json` must work without `rich` installed. Other subcommands (`ls`, `cost`, `tail`, `init`, `_hook`) remain stdlib-only.

## User Story

As a developer using AI coding agents (running `agentlog`-instrumented Claude Code sessions and scripted SDK runs)
I want to inspect a single run end-to-end on one screen — the configuration, the chronological tool calls the agent made, and the dollar breakdown
So that I can verify the agent actually did what it claimed (DESIGN.md use case #2), spot expensive runs (use case #1), and have a hero screenshot to put at the top of the README that immediately communicates what `agentlog` is for.

## Problem Statement

Items #1–#5 capture and roll up runs but offer no per-run inspection surface. `agentlog ls` shows a one-line summary; `agentlog cost <id>` shows only the dollar breakdown; neither answers "what did the agent DO?" The on-disk `events.jsonl` is the source of truth, but reading raw JSONL is hostile to humans. Without a focused, single-screen view, the project's most compelling pitch — "you can finally see what your agent actually did" — has no visual artifact, and the README has no hero screenshot.

## Solution Statement

Add a new module `src/agentlog/view.py` and wire its subcommand into `src/agentlog/cli.py`:

- A `run_view(*, run_id, limit, events_only, no_truncate, as_json) -> int` public entry that loads `state.json` (rc=2 if missing), then `events.jsonl` and `cost.json` (degrade gracefully — placeholders in their respective panels).
- Three rendering helpers (`_render_header`, `_render_timeline`, `_render_cost_footer`) that each take the loaded data and a `rich.console.Console` and emit one section. The `rich` import is gated inside the non-`--json` branch.
- A `_TOOL_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]]` dispatch table (lesson #9) covering Read, Edit, Write, Grep, Bash, Glob, with a default-fallback for unknown tools.
- Per-event-kind summary functions producing the inline summary strings (80-char cap for prompt/assistant_text, 60-char cap for tool_use, full for stop/session_start/session_end/unknown). `--no-truncate` disables the display cap.
- An ANSI-escape stripper applied to all event text before rendering (`re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)`) so a malicious or buggy payload cannot corrupt the terminal.
- A JSON formatter for `--json` mode that emits `{run_id, state, cost: {totals, computed, pricing_source}, events: [...]}`, reusing `cost._compute_run_cost` and `cost._resolve_pricing` for the `computed`/`pricing_source` fields.
- A small `_run_view(args)` shim in `cli.py` mirroring the `_run_cost` / `_run_ls` shape; removal of `"view"` from `_STUB_SUBCOMMANDS`; addition of an `elif name == "view":` branch in the subparser registration loop.
- A new `tests/test_view.py` covering all acceptance criteria, the sort-order regression test from ADW lesson #1, the dispatch-table-key invariant from lesson #2/#9, and the ANSI-safety edge case.

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` — v0.1 ship scope (item #6 = `agentlog view <id>` = the hero artifact). Use cases #1 (cost) and #2 (did-the-agent-do-it) drive the rendering priorities.
- `CLAUDE.md` — hard rules. Most relevant for `view`: rule #6 (local-first, no network), implicit corollary that reader CLIs are read-only with respect to `runs/`, and rule #2 fail-open does NOT apply (this is a user-invoked CLI; fail-loud like `ls`/`cost`).
- `docs/adw-lessons.md` — lessons #1 (sort-key regression test), #2 (no module-level asserts), #4 (purge stale "future" / `_STUB_SUBCOMMANDS` references), #8 (stylistic vs spec — gated `rich` import inside a function is intentional), #9 (dispatch dict for the per-tool summarizer), #11 (named regression tests).
- `ai_docs/research/cb153ac3-view-tui-analysis.md` — pre-planning research for this feature; component map, schema fields consumed, rich-gating placement, per-tool summarizer shape, full risk list, build-phase ordering. Referenced in this plan.
- `src/agentlog/cli.py` — current entry point. Line 12 `SUBCOMMANDS` includes `"view"`; line 14 `_STUB_SUBCOMMANDS = frozenset({"view"})` must shrink to `frozenset()`. Add `elif name == "view":` subparser branch after the `cost` branch; add `_run_view(args)` shim.
- `src/agentlog/_constants.py` — reuse `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SCHEMA_VERSION`, `SELF_LOG_NAME`, `SOURCE_HOOKS`, `SOURCE_SDK`. No new constants expected.
- `src/agentlog/cost.py` — import `_compute_run_cost`, `_resolve_pricing`, `_PricingError`, `BUILTIN_PRICING_PER_MILLION`, `_TOKEN_KIND_LABELS`, `_KIND_DISPLAY_ORDER`, `_PRICING_STALENESS_FOOTER`. The strict DAG (ls → cost → view) means no circular-import risk.
- `src/agentlog/ls.py` — import `_format_duration` and `_started_display` for the header card. Reference for the rich-import gating pattern (`_format_rich` at lines 414–440), though `view`'s gate is harder (rc=1 + install hint, not silent fallback).
- `src/agentlog/capture.py` — read-only reference for the hooks-side event schema (`session_start`, `prompt`, `tool_use`, `stop`, `session_end`, `unknown`). `params_summary` is the JSON-serialised `tool_input` dict; the summarizer must `json.loads` it.
- `src/agentlog/tail.py` — read-only reference for the SDK-side event schema (adds `assistant_text`; `stop` carries `duration_ms` and `total_cost_usd`).
- `tests/test_cli_smoke.py` — line 25's parametrise exclusion list currently excludes `{"init", "uninstall", "tail", "ls", "cost"}`. Add `"view"` so the "not yet implemented" assertion does not fire on the now-implemented subcommand (lesson #4).
- `tests/fixtures/sdk_minimal.jsonl` — re-ingest in test setup to produce a real `runs/<id>/` for end-to-end view rendering tests.
- `tests/test_cost.py` and `tests/test_ls.py` — references for `_seed_sdk_run` / `_seed_hooks_run` helpers; same pattern reused for `tests/test_view.py`.
- `pyproject.toml` — `[project.optional-dependencies] tui = ["rich>=13.7"]` already present. `dependencies = []` stays empty. No edits.

### New Files

- `src/agentlog/view.py` — main module (~400–500 lines). Public surface: `run_view(*, run_id, limit, events_only, no_truncate, as_json) -> int`. Module constants: `_TOOL_SUMMARIZERS`, `_EVENT_KIND_STYLES`, `_EVENT_KIND_ORDER_FOR_PADDING`, `_DISPLAY_CAP_TEXT = 80`, `_DISPLAY_CAP_TOOL = 60`. Internals: `_data_root`, `_log_self` (duplicated from cost.py — see lesson per project precedent), `_load_state`, `_load_events`, `_strip_ansi`, `_summarize_event`, `_summarize_tool_use`, `_render_header_rich`, `_render_timeline_rich`, `_render_cost_footer_rich`, `_render_json`, `run_view`.
- `tests/test_view.py` — ~25–35 test functions, one per acceptance criterion and edge case. Includes the lesson-#1 sort-order regression test (`test_view_timeline_renders_older_event_above_newer`) and the lesson-#9 dispatch-table key invariant (`test_view_tool_summarizers_table_keys_match_documented_set`).
- `docs/feature-cb153ac3-view-tui.md` — docs-phase deliverable (NOT created during build; placeholder only). Must include an actual rendered example against an existing `runs/sdk-*` run dir, per acceptance criteria.

## Implementation Plan

### Phase 1: Foundation

Build the `view.py` skeleton — module docstring with pinned invariants (read-only with respect to `runs/`, fail-loud user CLI, stdlib + `rich` gated inside `run_view`, no network), duplicated `_data_root` / `_log_self` helpers matching `cost.py`'s precedent, `from __future__ import annotations`, full type signatures. Define `_TOOL_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]]`, `_EVENT_KIND_STYLES: dict[str, str]`, and the cap constants (`_DISPLAY_CAP_TEXT = 80`, `_DISPLAY_CAP_TOOL = 60`). Wire a stub `run_view` that simply prints `view ok` and registers the subparser in `cli.py`. Remove `"view"` from `_STUB_SUBCOMMANDS`. Update `tests/test_cli_smoke.py` exclusion list. Verify `py_compile` + `agentlog view --help` work before adding logic.

### Phase 2: Core Implementation

Implement the read path: `_load_state(run_dir)` (rc=2 if missing), `_load_events(run_dir)` (returns `[]` if missing — sorts events by `timestamp` ascending EXPLICITLY, never trusting file order; lesson #1), `_load_cost(run_dir)` (returns `None` if missing). Implement the per-event summarizers, the ANSI-stripper, and the per-tool dispatch table. Implement the three rich renderers (`_render_header_rich`, `_render_timeline_rich`, `_render_cost_footer_rich`) one at a time, eyeballing against a real `runs/sdk-f50fb891-*` directory at each step until the output matches the spec mockup. Implement `_render_json` (combined object: `{run_id, state, cost: {totals, computed, pricing_source}, events}`); this path must NOT import `rich`. Implement `run_view(...)` orchestrator with the rich-gate placed AFTER the `--json` branch, the missing-state rc=2 path, and the missing-events/missing-cost graceful-degradation paths.

### Phase 3: Integration

Wire `cli.py`: remove `"view"` from `_STUB_SUBCOMMANDS`, add the `elif name == "view":` branch mirroring the `cost` shape (positional `run_id` required, `--limit` int default 100, `--events-only` action=store_true, `--no-truncate` action=store_true, `--json` dest=`as_json` action=store_true), add the `_run_view(args)` shim that calls `view.run_view(...)`. Extend `tests/test_cli_smoke.py`'s exclusion set to include `"view"`. Write `tests/test_view.py` covering every acceptance criterion and edge case. Run the full suite (`pytest` — must keep all 198 existing tests green), `ruff check src tests`, `mypy --strict`. Manually invoke `agentlog view <real-sdk-run-id>` against the existing `runs/sdk-f50fb891-*` and eyeball the output against the spec mockup; iterate on padding/alignment until it looks like a README screenshot.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Create the `view.py` skeleton and wire the CLI

- Create `src/agentlog/view.py` with: `from __future__ import annotations`, module docstring listing the invariants (read-only, fail-loud, stdlib + rich-gated, no network) as a numbered list mirroring `cost.py:1-20`.
- Add duplicated `_data_root()` and `_log_self(root, msg)` helpers matching `cost.py:45-60`. Do NOT factor into a shared module (lesson: tail.py:14 / cost.py:41 precedent — `_io.py` deferred to v0.2+).
- Stub `run_view(*, run_id: str, limit: int, events_only: bool, no_truncate: bool, as_json: bool) -> int` that prints `view ok` and returns 0.
- In `src/agentlog/cli.py`: change `_STUB_SUBCOMMANDS = frozenset({"view"})` to `_STUB_SUBCOMMANDS: frozenset[str] = frozenset()`. Add `from agentlog import view` to the imports at line 10. Insert an `elif name == "view":` branch after the `cost` branch (line 181). Register the positional `run_id` and the four optional flags. Add `_run_view(args)` shim mirroring `_run_cost`.
- Edit `tests/test_cli_smoke.py` line 25: add `"view"` to the exclusion list so the `not yet implemented` assertion is parametrised over the now-empty residual set.
- Run `.venv/bin/python -m py_compile src/agentlog/view.py src/agentlog/cli.py && echo "OK"`.
- Run `.venv/bin/agentlog view --help` to confirm the subparser registers cleanly.
- Run the existing test suite (`pytest`); expect zero regressions even though `view` is still a stub.

### 2. Implement the read-path helpers

- Add `_load_state(run_dir: Path, root: Path) -> dict[str, Any] | None` — reads `state.json`, returns `None` if missing, parses tolerantly (mirror `cost._read_json_safe`).
- Add `_load_events(run_dir: Path, root: Path) -> list[dict[str, Any]]` — reads `events.jsonl` line-by-line, skips malformed lines (log to `_self.log`), returns `[]` if file missing. **Sort by `timestamp` ascending EXPLICITLY** using `datetime.fromisoformat(...)` with a `datetime.min`-replace-with-UTC fallback for malformed timestamps (lesson #1). Document the sort key in an inline comment.
- Add `_load_cost(run_dir: Path, root: Path) -> dict[str, Any] | None` — reads `cost.json`, returns `None` if missing.
- Add `_strip_ansi(s: str) -> str` — uses `re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)`. Apply to ALL event text fields before rendering. Reused unit-tested function (one test that round-trips a `\x1b[2J\x1b[H` payload).

### 3. Implement the per-tool summarizer dispatch

- Define `_TOOL_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]]` at module top level. Keys: `"Read"`, `"Edit"`, `"Write"`, `"Grep"`, `"Bash"`, `"Glob"`. Each value extracts the relevant field from the parsed `tool_input` dict.
  - `Read`/`Edit`/`Write` → `params.get("file_path") or "?"`
  - `Grep` → `f"{params.get('pattern', '?')!r} in {params.get('path', '.')}"`
  - `Bash` → `params.get("command", "?")` truncated to 60 chars
  - `Glob` → `params.get("pattern", "?")`
- Add `_summarize_tool_use(record: dict[str, Any], *, cap: int | None) -> str` — parses `record["params_summary"]` via `json.loads` inside a try/except `(json.JSONDecodeError, TypeError)`. On failure or non-dict result, fall back to the raw `params_summary` truncated to `cap` (or 60). On success, dispatch via `_TOOL_SUMMARIZERS.get(tool, _default_summarizer)` and apply the cap.
- Add a `_default_summarizer(params: dict[str, Any]) -> str` that JSON-dumps the params dict and truncates — covers unknown tool names.
- Hoist tool-name padding into a module constant `_TOOL_NAME_PAD = 8`.

### 4. Implement the per-event summarizers

- Add `_summarize_event(record: dict[str, Any], *, no_truncate: bool) -> str` that dispatches on `record["event"]`:
  - `session_start` → `f"cwd={record.get('cwd') or '?'}"` (no cap; cwd already short).
  - `prompt`/`assistant_text` → strip ANSI from `record.get("text") or ""`, cap at `_DISPLAY_CAP_TEXT` (80 chars) unless `no_truncate`, append `…` if capped.
  - `tool_use` → `f"{record.get('tool', '?'):<{_TOOL_NAME_PAD}}  {_summarize_tool_use(record, cap=_DISPLAY_CAP_TOOL if not no_truncate else None)}"`.
  - `stop` → `f"{_format_duration_ms(record.get('duration_ms'))} elapsed | {_total_tokens(record.get('usage'))} tokens"`. Total = sum of input/output/cache_read/cache_creation.
  - `session_end` → `record.get("summary") or ""` (full text, no cap).
  - `unknown` → `f"original_type={record.get('original_type') or record.get('original_event') or '?'} (raw size: {_raw_size(record)})"`.
- Add `_format_duration_ms(ms: int | None) -> str` — converts to `1h23m45s` style matching `ls._format_duration`'s vocabulary; returns `"-"` if `ms` is None.

### 5. Implement the three rich renderers

- Inside `run_view`, AFTER the `--json` branch returns, gate the rich import:
  ```python
  try:
      import rich.box
      from rich.console import Console
      from rich.panel import Panel
      from rich.text import Text
  except ImportError:
      print(
          "agentlog view requires the 'rich' library. Install with:\n"
          "    pip install 'agentlog[tui]'\n"
          "    # or:\n"
          "    uv pip install 'agentlog[tui]'",
          file=sys.stderr,
      )
      return 1
  ```
  Add a one-line comment: `# Gated import: --json mode must work without rich installed.` (lesson #8 reviewer pre-emption.)
- `_render_header_rich(console, state, event_count, cost_total_str)` — build a `Panel` titled with the run_id, box=`rich.box.HEAVY`, body lines formatted as `Source: …`, `Model: …`, `Cwd: …`, plus a paired row for `Started:`/`Duration:` and `Events:`/`Cost:` (right-align the value column on those paired rows so they line up). Use `_started_display` and `_format_duration` from `ls.py`.
- `_render_timeline_rich(console, events, *, limit, no_truncate)` — print `TIMELINE` header, then iterate events (already sorted ascending). Each row: `<HH:MM:SSZ>  <event_kind padded>  <summary>`, prefixed with `┌`/`│`/`└` left-rail characters (first row gets `┌`, last gets `└`, middle gets `│`). Pad event-kind names to the longest known kind (`assistant_text` = 14 chars). Color via `Text(kind, style=_EVENT_KIND_STYLES.get(kind, "dim"))`. If `limit > 0` and `len(events) > limit`, show only the first `limit` events and append `… (N more events; use --limit 0 to see all)`. Render assistant_text/prompt/tool_use summaries inside `Text(value, no_wrap=False)` so rich does not interpret leftover ANSI (we also pre-stripped via `_strip_ansi`).
- `_render_cost_footer_rich(console, cost_record)` — print `COST` header, then call the same four-row breakdown logic used by `cost._format_single_plain` lines 384-465 (call into `cost._compute_run_cost` and reuse `_TOKEN_KIND_LABELS` / `_KIND_DISPLAY_ORDER` / `_PRICING_STALENESS_FOOTER`). If `cost_record is None` or `cost_record["totals"]` is empty, print `no cost data recorded` instead.

### 6. Implement `--json` mode

- Add `_render_json(run_dir, state, events, cost_record, pricing, pricing_source_tag, root) -> str` — builds `{run_id, state, cost, events}`. The `cost` sub-object includes the verbatim `cost.json` `totals`, the per-kind computed dollar amounts from `cost._compute_run_cost`, and `pricing_source`. The `events` array is the events.jsonl contents (already sorted ascending). NO truncation in JSON mode.
- In `run_view`: validate the run exists BEFORE building any JSON (rc=2 + stderr message, NO partial JSON output). Wire the `--json` branch to call `_render_json` and print the result, returning 0.
- Verify the `--json` path never references `rich` (gate the import strictly below it). Add a test that imports `agentlog.view` with `sys.modules["rich"] = None` and calls `run_view(..., as_json=True)` — must succeed.

### 7. Wire all the flags together in `run_view`

- Mutually-exclusion-free in v0.1 — all flags compose. Validate input shape: `limit` must be `>= 0` (argparse type=int handles this; reject negatives with rc=2 if needed).
- Resolve `run_dir = _data_root() / RUNS_DIR_NAME / run_id`. If `state.json` missing → `print("agentlog view: error: run id 'X' not found at <path>", file=sys.stderr); return 2`.
- If `--json`: build state/events/cost + computed cost via `cost._compute_run_cost` → print JSON → return 0. **Do NOT import `rich`.**
- Else: gate `rich` import (return 1 + hint on `ImportError`). Build a `Console(highlight=False)` writing to `sys.stdout`.
- If not `events_only`: call `_render_header_rich`.
- Always: call `_render_timeline_rich(console, events, limit=limit, no_truncate=no_truncate)`. If `events_only`, no header/footer was/will be drawn — still render the timeline.
- If not `events_only`: call `_render_cost_footer_rich`.
- Wrap the main body in `try: ... except (OSError, ValueError) as exc: _log_self(root, f"view: unexpected error: {exc!r}"); print(f"agentlog view: error: {exc}", file=sys.stderr); return 1`.

### 8. Lesson-#4 / Lesson-#9 / Lesson-#11 audit pass

- `grep -nE "not[_ ]yet[_ ]implemented|_STUB|will replace|future|TODO|FIXME" src/agentlog/view.py src/agentlog/cli.py tests/test_view.py tests/test_cli_smoke.py` — should return only intentional matches. Specifically, `_STUB_SUBCOMMANDS = frozenset()` should be the ONLY remaining `_STUB` reference, and no test asserts `view` is "not yet implemented".
- Confirm `_TOOL_SUMMARIZERS` keys match the documented set in a unit test (`test_view_tool_summarizers_table_keys_match_documented_set`), NOT via a module-level `assert` (lesson #2).
- Confirm the sort-order regression test exists (`test_view_timeline_renders_older_event_above_newer`), seeding two events with deliberately reversed timestamps and asserting position (lesson #1 — see Test shape below).

### 9. Write `tests/test_view.py`

- One `_seed_run_dir` helper that writes a minimal `state.json` + `events.jsonl` + `cost.json` under a `tmp_path` `AGENTLOG_HOME` (set via `monkeypatch.setenv`). Mirrors `tests/test_cost.py` helper shape.
- Cover every acceptance criterion + edge case (see Testing Strategy section below).

### 10. Run the full validation chain

- `.venv/bin/pytest -x -q` — must pass all existing 198 tests + the new `test_view.py` tests with zero regressions.
- `.venv/bin/ruff check src tests` — must be clean.
- `.venv/bin/mypy --strict src tests` — must be clean.
- Manually run `.venv/bin/agentlog view <real-sdk-run-id>` against an existing `runs/sdk-f50fb891-*` directory. Eyeball the output against the spec mockup. Adjust padding, alignment, color, panel borders until it looks like a README screenshot. THIS IS THE BUILD-PHASE QUALITY GATE — do not declare done until the output is visually clean.
- Verify `agentlog view <id> | less -R` renders correctly (ANSI preserved, no terminal corruption).
- Verify `pip uninstall rich` (in a throwaway venv, OR by monkeypatching `sys.modules["rich"] = None` in a test) → `agentlog view <id>` returns rc=1 with the install hint, and `agentlog view <id> --json` still succeeds.

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation:

- `tests/test_cost.py` and `tests/test_ls.py` are the templates. Both use a `_seed_*_run(tmp_path, monkeypatch, ...)` helper that sets `$AGENTLOG_HOME` to a temp dir and writes the canonical `state.json` + `events.jsonl` + `cost.json` shape. Reuse that pattern.
- No `HOW_TO_CREATE_TESTS.md` / `TESTING.md` exists; the codebase follows pytest conventions established in `tests/test_capture.py` → `tests/test_cost.py`.
- Use `capsys` / `tmp_path` / `monkeypatch` fixtures. Never hardcode paths or `AGENTLOG_HOME` values.
- Test function names follow `test_<feature>_<scenario>_<observable_property>` (lesson #11).

### Unit Tests

- `test_view_tool_summarizers_table_keys_match_documented_set` — assert `set(_TOOL_SUMMARIZERS) == {"Read", "Edit", "Write", "Grep", "Bash", "Glob"}` (lesson #2/#9 invariant).
- `test_view_summarize_tool_use_<Read|Edit|Write>_returns_file_path` — three tests, one per tool, asserting the extracted summary contains the file_path.
- `test_view_summarize_tool_use_Grep_returns_pattern_in_path` — asserts `pattern` and `path` both appear.
- `test_view_summarize_tool_use_Bash_truncates_long_commands_at_60_chars` — assert the output ends with `…` for a 200-char command.
- `test_view_summarize_tool_use_Glob_returns_pattern` — asserts the glob pattern.
- `test_view_summarize_tool_use_unknown_tool_falls_back_to_raw_params` — pass `tool="Unknown"`, assert it returns the raw JSON dump truncated to 60 chars.
- `test_view_summarize_tool_use_malformed_params_summary_returns_truncated_raw_string` — pass `params_summary="{not valid json"`, assert no exception + returns the raw string truncated.
- `test_view_strip_ansi_removes_escape_sequences` — pass `\x1b[2J\x1b[Hhello\x1b[31mworld\x1b[0m`, assert output is `"helloworld"`.
- `test_view_default_summarizer_truncates_at_60_chars` — assert the unknown-tool fallback truncates.
- `test_view_format_duration_ms_handles_none` — assert `_format_duration_ms(None) == "-"`.
- `test_view_format_duration_ms_renders_hours_minutes_seconds` — assert `_format_duration_ms(14184000)` produces `"3h56m24s"` or equivalent.

### Integration Tests

- `test_view_happy_path_renders_three_panels` — seed a run with one of each event kind, invoke `run_view(...)`, assert stdout contains the run id (header), each event kind label (timeline), and `Cost`/`Input`/`Output`/`Total` (cost footer).
- `test_view_events_only_skips_header_and_cost` — invoke with `events_only=True`, assert the output does NOT contain the run id title or the `Total` row.
- `test_view_no_truncate_shows_full_assistant_text` — seed an `assistant_text` event with a 200-char `text` field, invoke with `no_truncate=True`, assert the full 200 chars appear in output.
- `test_view_default_truncates_assistant_text_at_80_chars` — same fixture, invoke with `no_truncate=False`, assert only 80 chars + `…` appear.
- `test_view_limit_5_shows_more_hint` — seed 10 events, invoke with `limit=5`, assert output contains the first 5 timestamps AND `… (5 more events; use --limit 0 to see all)`.
- `test_view_limit_0_shows_all_events` — seed 10 events, invoke with `limit=0`, assert all 10 timestamps appear in output.
- `test_view_json_mode_emits_combined_object_without_rich` — set `monkeypatch.setitem(sys.modules, "rich", None)` and equivalent for `rich.box`/`rich.console`/`rich.panel`/`rich.text` (or use `importlib.reload`), invoke with `as_json=True`, assert `json.loads(stdout)` has keys `{"run_id", "state", "cost", "events"}`.
- `test_view_json_cost_includes_pricing_source` — assert `result["cost"]["pricing_source"] in {"builtin", "missing"}` (or `file:<path>`).
- `test_view_unknown_model_renders_double_question_marks_in_footer` — seed `state.json` with `model="claude-future-1-0"`, invoke, assert `??` appears in the cost footer.
- `test_view_missing_state_returns_rc_2` — seed run dir with only `events.jsonl`, invoke, assert rc=2 + stderr contains `not found`.
- `test_view_missing_events_renders_header_and_cost` — seed only `state.json` + `cost.json`, invoke, assert rc=0 + `no events recorded` placeholder appears in timeline section.
- `test_view_missing_cost_renders_header_and_timeline` — seed only `state.json` + `events.jsonl`, invoke, assert rc=0 + `no cost data recorded` placeholder appears in cost section.
- `test_view_zero_events_renders_empty_timeline_section` — seed `events.jsonl` as an empty file, invoke, assert rc=0 + the timeline section header still prints + a placeholder line.
- `test_view_nonexistent_run_id_returns_rc_2` — invoke against a run id that does not exist, assert rc=2 + stderr matches `run id 'X' not found at <path>`.
- `test_view_json_against_nonexistent_run_returns_rc_2_with_no_partial_output` — same as above with `as_json=True`, assert rc=2 AND stdout is empty (no partial JSON).
- `test_view_missing_rich_returns_rc_1_with_install_hint` — `monkeypatch.setitem(sys.modules, "rich", None)` (and rich submodules), invoke with `as_json=False`, assert rc=1 + stderr contains `pip install 'agentlog[tui]'`.
- `test_view_pipes_to_less_R_without_terminal_corruption` — capture stdout, assert no embedded `\x1b[2J` (clear-screen) or `\x1b[?` (private mode) sequences appear; ANSI color codes are fine.
- `test_view_cli_subcommand_help_lists_all_flags` — invoke `cli.main(["view", "--help"])`, assert each of `--limit`, `--events-only`, `--no-truncate`, `--json` appears in the help output.

### Edge Cases

- **Lesson #1 sort-key regression (REQUIRED — copy this Test shape verbatim into the test file):**
  ```python
  def test_view_timeline_renders_older_event_above_newer() -> None:
      # Seed two events with deliberately reversed file order: write the
      # NEWER one to events.jsonl first. View MUST sort by `timestamp`
      # ascending, so OLDER must appear above NEWER in output.
      _seed_run_dir(...)  # write events out of chronological order
      ...
      rc = run_view(run_id=..., limit=0, events_only=True, no_truncate=True, as_json=False)
      out = capsys.readouterr().out
      pos_older = out.find("08:00:00Z")
      pos_newer = out.find("09:00:00Z")
      assert pos_older < pos_newer, "expected older event above newer under timeline sort"
      assert rc == 0
  ```
- ANSI escape sequences in `assistant_text` — seed `text` containing `\x1b[2J\x1b[H`, render, assert output does NOT contain `\x1b[2J` (stripped).
- `params_summary` is a truncated/malformed JSON string — fall back to raw truncated.
- `params_summary` is an empty `{}` — render the empty-dict fallback gracefully.
- `tool_use` with `tool=""` (empty string) — render as `?` in tool column, fall back to default summarizer.
- `events.jsonl` containing a line with malformed JSON — log to `_self.log`, skip the line, render the rest.
- `state.json` with `schema_version=2` — log warning, continue rendering with available fields (parity with `cost._compute_run_cost`).
- `cost.json` with `schema_version=2` — same handling.
- `--limit -1` → argparse type=int accepts; reject inside `run_view` with rc=2 + stderr message (or accept; document either way and test).
- Very long tool name (e.g. `MultiFooBarBaz`) — render without breaking column alignment (either truncate to 8 chars + `…` or accept misalignment for that row; pick one and test).
- A run with model `null` in state.json — header `Model:` shows `-`, cost footer shows `??`.

## Acceptance Criteria

- `.venv/bin/agentlog view <real-run-id>` (id from `agentlog ls`) renders the three-panel layout in a TTY with `rich` installed.
- Header panel shows the run id as title, `Source` / `Model` / `Cwd` / `Started` / `Duration` / `Events` / `Cost`. `Duration:` and `Cost:` line up vertically on the right-aligned paired row.
- Timeline section renders one row per event, color-coded by kind (session_start/session_end bold-magenta, prompt cyan, assistant_text green, tool_use yellow, stop bold-blue, unknown dim), padded so all event-kind names align under each other.
- `tool_use` rows show the extracted summary (`file_path` for Read/Edit/Write, `pattern in path` for Grep, first 60 chars of `command` for Bash, `pattern` for Glob).
- Cost footer renders the same four-row breakdown shape as `agentlog cost <id>` plain output, with `??` for unknown models.
- `agentlog view <id> --limit 5` shows the first 5 events (chronological) + `… (N more events; use --limit 0 to see all)`.
- `agentlog view <id> --limit 0` shows all events without a truncation hint.
- `agentlog view <id> --events-only` renders ONLY the timeline (no header panel, no cost footer).
- `agentlog view <id> --no-truncate` renders the full event text (no 80-char cap on prompt/assistant_text; no 60-char cap on tool_use summaries).
- `agentlog view <id> --json` emits the documented combined JSON object (`{run_id, state, cost: {totals, computed, pricing_source}, events: [...]}`), works WITHOUT `rich` installed.
- `agentlog view nonexistent-id` returns rc=2 with stderr `agentlog view: error: run id 'nonexistent-id' not found at <path>`.
- `agentlog view <id>` without `rich` installed returns rc=1 + the install hint (verifiable via `monkeypatch.setitem(sys.modules, "rich", None)`).
- `agentlog view <id> --json` against a missing run returns rc=2 + stderr message + NO partial JSON on stdout.
- Output piped to `less -R` displays cleanly (ANSI color codes preserved, no terminal corruption from clear-screen or cursor-positioning sequences in event payloads).
- A `_TOOL_SUMMARIZERS` dispatch table exists as `dict[str, Callable]` with keys `{Read, Edit, Write, Grep, Bash, Glob}` (lesson #9).
- A `test_view_timeline_renders_older_event_above_newer` regression test exists and passes (lesson #1).
- A `test_view_tool_summarizers_table_keys_match_documented_set` invariant test exists and passes (lesson #2/#9).
- An ANSI-escape safety test exists and passes (lesson — explicit attack-vector mitigation).
- The `_STUB_SUBCOMMANDS` set in `cli.py` is `frozenset()`; `tests/test_cli_smoke.py:25`'s exclusion list includes `"view"` (lesson #4).
- All 198 existing tests still pass; `ruff check src tests` and `mypy --strict src tests` are clean.
- `pyproject.toml dependencies = []` stays empty; `rich` remains in the `tui` extra only.
- README is NOT touched in the build phase; the docs phase generates `docs/feature-cb153ac3-view-tui.md` AND includes a captured screenshot (or ASCII rendering) showing the actual output against an existing `runs/sdk-f50fb891-*` run.

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These run during the build phase — do NOT include pytest, linters, or pipeline runs (those belong to dedicated CI phases).

- `.venv/bin/python -m py_compile src/agentlog/view.py src/agentlog/cli.py && echo "OK"` — verify no syntax errors in the new module or the CLI wiring.
- `.venv/bin/python -c "from agentlog import view; print('import OK')"` — verify the module imports cleanly without `rich` installed (the `rich` import must be gated inside `run_view`, not at module top level).
- `.venv/bin/python -c "from agentlog.view import run_view, _TOOL_SUMMARIZERS; print(sorted(_TOOL_SUMMARIZERS))"` — verify the dispatch table exists with the documented keys.
- `.venv/bin/agentlog --help | grep view` — verify the subcommand registers in the top-level help.
- `.venv/bin/agentlog view --help` — verify all four flags (`--limit`, `--events-only`, `--no-truncate`, `--json`) appear in the subcommand help.

## Notes

- **No new runtime dependencies.** `pyproject.toml dependencies = []` stays empty; `rich` lives in the existing `[project.optional-dependencies] tui` extra. Users who installed via `pip install agentlog` (no extras) get a clear `pip install 'agentlog[tui]'` hint on `view`, while `view --json` continues to work for them.
- **Privacy / local-first.** No network calls. `view` is purely a reader of local `runs/<id>/` files. The `--json` output mode emits the same data already on disk; nothing is sent anywhere.
- **Read-only contract.** `view` MUST NOT mutate `state.json`, `events.jsonl`, `cost.json`, or the SQLite index from `ls`. Open files read-only. A regression test could `os.chmod(run_dir, 0o555)` and assert `view` still renders cleanly — optional but cheap.
- **Visual-quality gate (build phase, not docs phase).** The README hero screenshot bar means padding/alignment/color choices that look "fine" in dev but ugly in a screenshot are a failure. The build step #10 mandates eyeballing the output against the real `runs/sdk-f50fb891-*` directory and iterating before declaring done. The docs phase later captures the actual screenshot for `docs/feature-cb153ac3-view-tui.md`.
- **Out of v0.1 scope (deferred to v0.2+ per DESIGN.md):**
  - Interactive `textual` TUI (keyboard navigation, panes, search).
  - `agentlog replay <id>` (step-through event playback).
  - `agentlog diff <a> <b>` (side-by-side run comparison).
  - Per-event filtering (`--only tool_use`, `--exclude assistant_text`) — `view --json | jq` covers the scripting case.
  - Embedded code-diff rendering from `tool_use` Edit calls (would require running edits against current file content; semantic per-tool work too large for v0.1).
- **Architectural call NOT to make:** do not factor a shared `agentlog/_render.py` out of `cost.py` / `view.py` to "DRY up" formatters. They have different layout intent (cost = 4-column data table; view = header card + tree + compact subtable) and the duplication-tolerance precedent (cost.py:41 comment) is the project's established taste.
- **Reviewer pre-emption (lesson #8):**
  - `from rich import ...` inside `run_view` is intentional — `# Gated import: --json mode must work without rich installed.`
  - `_data_root()` / `_log_self()` duplicated from `cost.py`/`ls.py`/`tail.py` — `# Duplicated helpers; shared _io.py deferred to v0.2+ (precedent: cost.py:41, tail.py:14).`
  - `_TOOL_SUMMARIZERS` as a top-level dict (not a class) follows the established `capture._DISPATCH` / `tail._RECORD_TRANSLATORS` shape.
  - Broad `except (OSError, ValueError)` matches `cost.py:781`, `ls.py:517`, `tail.py:469` precedents.
