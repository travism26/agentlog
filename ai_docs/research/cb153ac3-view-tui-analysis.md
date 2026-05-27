# Research: `agentlog view <id>` TUI — Hero Artifact

## Metadata

adw_id: `cb153ac3`
prompt: `/tmp/agentlog_step6_prompt.md` — implement v0.1 ship-scope item #6 (`agentlog view <id>`), the static-`rich` three-panel renderer that becomes the README screenshot.
date: `2026-05-27`

## Executive Summary

`view` is a leaf, read-only consumer of `runs/<id>/{state.json, events.jsonl, cost.json}` — every upstream producer (`capture`, `tail`, `cost`) is already landed and the on-disk format is stable. The build is mostly composition: lift `_compute_run_cost`/`_load_pricing` from `cost.py` for the footer, lift `_format_duration`/`_started_display` from `ls.py` for the header, and add a new dispatch table for per-tool summary extraction. The two biggest risks are (a) gating the `rich` import correctly so `--json` works without it AND base installs see only a clean install-hint, and (b) honoring ADW lessons #1 (timeline sort regression test) and #4 (purge the `_not_implemented` stub references in `cli.py`/tests) so the polish pass doesn't re-find them.

## Existing Architecture

### Relevant Documentation Found

- `DESIGN.md` — locked v0.1 design. Item #6 explicitly named the **hero artifact**. Use case #2 ("did the agent actually do what it claimed") is the load-bearing reason `tool_use` rows must show extracted params, not raw JSON.
- `CLAUDE.md` — non-negotiable rules. Most relevant for `view`: rule #6 (local-first, no network) and the implicit corollary that downstream readers must be **read-only** with respect to `runs/`. Rule #2 (fail-open) does NOT apply: `view` is a user-invoked CLI, fail-loud like `ls`/`cost`/`tail`.
- `docs/adw-lessons.md` — all 11 lessons; the ones that apply to this task:
  - **#1 sort keys** — timeline renders events in `started_at` ascending; needs an explicit two-event regression test asserting the older row appears above the newer row.
  - **#2 module-level `assert`** — applies if a `_TOOL_SUMMARIZERS` dispatch table is introduced; assert table-shape invariants in tests, not at module load.
  - **#4 stale "future" comments** — `cli.py:14` has `_STUB_SUBCOMMANDS = frozenset({"view"})`. After wiring `view`, this set must be removed (or emptied) AND `tests/test_cli_smoke.py:25` must drop `"view"` from its exclusion list. Grep changed files for "not yet implemented" / "future" after build.
  - **#5 sentinel strings** — `view` writes nothing to disk, so this rule is informational. The `--json` shape DOES become part of the documented surface (callers will pipe to `jq`), so treat the JSON keys as durable.
  - **#8 stylistic vs spec** — anticipate reviewer pushback on `from rich import ...` inside a function (E402-adjacent); document why with a one-liner comment ("gate the import so `--json` works without rich" → CLAUDE.md / spec requirement).
  - **#9 dispatch dict** — the per-tool summarizer is exactly the >3-elif pattern; build it as `_TOOL_SUMMARIZERS: dict[str, Callable[[dict], str]]` from day one.
  - **#11 named regression tests** — any reviewer-flagged behavior in polish pass needs a `test_view_<scenario>_<observable>` test.
- `ai_docs/research/355ec9b6-cost-rollup-analysis.md` — the precedent research for the most recently shipped consumer module (`cost.py`); the failure-contract framing and "duplicated helpers vs shared module" tradeoffs apply identically here.
- Prior feature docs `docs/feature-355ec9b6-cost-rollup.md`, `docs/feature-07ec0bb6-ls-unified-view.md` — show the docs-phase deliverable shape (`docs/feature-<adw_id>-view-tui.md` will live alongside them).

### Component Map

```
            ┌────────────────┐
            │ runs/<id>/     │   canonical on-disk format (written by capture/tail)
            │   state.json   │
            │   events.jsonl │
            │   cost.json    │
            └────┬─────────┬─┘
                 │         │
       reads only│ reads only
                 │         │
       ┌─────────▼─┐   ┌───▼──────────────────────┐
       │  ls.py    │   │  cost.py                 │
       │  - index  │   │  - _compute_run_cost     │
       │  - sqlite │   │  - _resolve_pricing      │
       │  - format │   │  - BUILTIN_PRICING       │
       └─────┬─────┘   └────────┬─────────────────┘
             │                  │
             │ exports:         │ exports (for view):
             │ _format_duration │ _compute_run_cost
             │ _started_display │ _resolve_pricing
             │                  │ BUILTIN_PRICING_PER_MILLION
             │                  │ _TOKEN_KIND_LABELS, _KIND_DISPLAY_ORDER
             │                  │
             └──────┬───────────┘
                    │
            ┌───────▼────────┐
            │  view.py (NEW) │   leaf module — no other code imports it
            │  - run_view    │
            │  - rich-gated  │
            │  - tool dispatch
            └───────┬────────┘
                    │
            ┌───────▼────────┐
            │  cli.py        │   wires `view` subcommand
            └────────────────┘
```

No circular-import risk: `cost.py` already imports from `ls.py` (line 38: `from agentlog.ls import _format_duration, _started_display`), so `view.py` doing the same is consistent. `view.py` importing from `cost.py` continues the pattern (cost depends on ls, view depends on cost — strict DAG).

### Key Files and Modules

| File | Lines | Why it matters for `view` |
| --- | --- | --- |
| `src/agentlog/cli.py` | 254 | `_STUB_SUBCOMMANDS = frozenset({"view"})` at line 14; subparser registration loop at 25-181; `_not_implemented` at 234-236. Pull `view` out of the stub set; add a real branch at line 134 (alongside `cost`); add a `_run_view` dispatcher at the bottom. |
| `src/agentlog/cost.py` | 785 | Source for `_compute_run_cost` (251-357), `_resolve_pricing` (186-226), `BUILTIN_PRICING_PER_MILLION` (69-100), `_KIND_DISPLAY_ORDER`/`_TOKEN_KIND_LABELS` (109-114, 364), `_PRICING_STALENESS_FOOTER` (102-105). Reusable, but watch out: the formatters (`_format_single_plain`, `_format_single_json`) build a full text table — `view` wants a compacter version, so re-rendering is appropriate (don't try to share the formatter). |
| `src/agentlog/ls.py` | 520 | Source for `_format_duration` (337-360) and `_started_display` (363-370). Also the precedent for rich-gated rendering — see `_format_rich` (414-440), which catches `ImportError` and falls back. `view`'s gate is stricter (rc=1 with hint instead of fallback) since rich is the whole point. |
| `src/agentlog/capture.py` | 428 | Defines the event schema. Important fields for `view` rendering: `event` (one of `session_start`/`prompt`/`tool_use`/`stop`/`session_end`/`unknown`), `timestamp` (ISO-8601 micros), `tool`+`params_summary` for tool_use rows, `text` for prompt/assistant_text, `usage` for stop. |
| `src/agentlog/tail.py` | 510 | Same event schema, SDK side. Adds `assistant_text` (capture.py doesn't emit this — only tail does, from `assistant.message.content[].type == "text"` blocks). This is why the prompt's color table mentions `assistant_text` — it's an SDK-only event kind. |
| `src/agentlog/_constants.py` | 43 | `SCHEMA_VERSION=1`, `EVENTS` tuple, `RUNS_DIR_NAME`, `DEFAULT_DATA_ROOT_NAME`. No new constants expected. |
| `tests/test_cli_smoke.py` | 32 | Parametrized test excludes implemented commands from the "not yet implemented" assertion. **Line 25 must be updated** to add `"view"` to the exclusion list. |
| `tests/fixtures/sdk_minimal.jsonl` | — | Existing tail fixture; can be re-ingested in a test setup to produce a real `runs/<id>/` directory for end-to-end view rendering tests. |
| `pyproject.toml` | — | `[project.optional-dependencies] tui = ["rich>=13.7"]` already present. No edits needed. |
| `DESIGN.md` lines 195, 315 | — | `view` is the hero artifact for the README. Visual quality bar is unusually high for v0.1. |
| `docs/adw-lessons.md` | 142 | All 11 lessons; see Phase 1 above. |

### Event-Kind → Renderer Mapping (derived from capture.py and tail.py)

| Event kind | Source field | Producer | Per-spec summary rule |
| --- | --- | --- | --- |
| `session_start` | `cwd` | both | show `cwd=...` |
| `prompt` | `text` | both (UserPromptSubmit + tail user-text) | first 80 chars |
| `assistant_text` | `text` | tail only | first 80 chars |
| `tool_use` | `tool` + `params_summary` (JSON string) | both | dispatch to `_TOOL_SUMMARIZERS[tool]` |
| `stop` | `usage` dict + `duration_ms` | both | `<dur> elapsed | <tokens> tokens` |
| `session_end` | `summary` | capture only | show summary if present |
| `unknown` | `original_event` / `original_type` + `raw` | both | show original type + raw byte size |

Capture vs tail disparities `view` must tolerate:
- `assistant_text` events never appear in hook-captured runs (capture has no equivalent event; assistant output is invisible to PostToolUse).
- `tail`'s `stop` carries `duration_ms` and `total_cost_usd`; capture's `stop` does not (only `usage`).
- `session_end` events are capture-only (tail has no equivalent hook).
- `tool_use.params_summary` is a JSON-serialized string of the original `tool_input` dict in both cases — the summarizer must `json.loads` it before dispatching.

## Affected Areas

### Files That Will Need Changes

| File | Change | Reason |
| --- | --- | --- |
| `src/agentlog/view.py` | **New** | All TUI rendering logic. Module docstring should list invariants like `cost.py` does (read-only, fail-loud, stdlib + rich gated). |
| `src/agentlog/cli.py` | Edit lines 12-14 + add `view` subparser branch + add `_run_view` | Remove `"view"` from `_STUB_SUBCOMMANDS`; register flags (`<run_id>`, `--limit`, `--events-only`, `--no-truncate`, `--json`); wire `args.func = _run_view`. |
| `tests/test_cli_smoke.py` | Line 25: add `"view"` to the exclusion list | The `not yet implemented` assertion would fire spuriously once `view` is real. (Lesson #4.) |
| `tests/test_view.py` | **New** | All `view`-specific tests: happy path, flags, edge cases, sort-order regression (lesson #1), tool dispatch table coverage, ANSI-escape safety. |
| `docs/feature-cb153ac3-view-tui.md` | **New** | Docs-phase deliverable. Must include an actual rendered example against an existing `runs/sdk-*` directory. |

### Files NOT changing (deliberate)

- `pyproject.toml` — `tui` extra already in place.
- `src/agentlog/_constants.py` — no new constants needed; `view` doesn't write any durable format.
- `src/agentlog/cost.py` / `src/agentlog/ls.py` — import-only consumers. Do NOT refactor cost/ls to "share" helpers with view — the duplication-tolerance precedent set by `cost.py` (line 41 comment: "Helpers duplicated from ls.py — inverted failure contract") is the project's established taste.
- `README.md` — explicitly out of scope per acceptance criteria; only updated by docs phase via the feature doc.

### Dependencies

**What `view.py` will depend on:**
- stdlib: `argparse`, `json`, `os`, `sys`, `datetime`, `pathlib`, `typing`, `collections.abc.Callable`
- `agentlog._constants`: `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `SCHEMA_VERSION`, `SELF_LOG_NAME`
- `agentlog.cost`: `_compute_run_cost`, `_resolve_pricing`, `_TOKEN_KIND_LABELS`, `_KIND_DISPLAY_ORDER`, `BUILTIN_PRICING_PER_MILLION`, `_PricingError` (for graceful pricing-file failure parity)
- `agentlog.ls`: `_format_duration`, `_started_display`
- `rich.*`: **gated** inside `run_view` non-JSON branch only

**What depends on `view.py`:** only `cli.py`. No transitive consumers.

### Integration Points

1. **`cli.py` subparser registration** — model after `elif name == "cost":` block (cli.py:134-181). The `<run_id>` positional + four optional flags should slot into the existing `for name in SUBCOMMANDS` loop cleanly.
2. **`$AGENTLOG_HOME` resolution** — duplicate `_data_root()` (same pattern as `cost.py:45-49`, `ls.py:44-48`, `tail.py:52-56`). DO NOT factor into a shared module — that's a v0.2+ refactor (DESIGN.md hint, tail.py:14 comment).
3. **`_self.log` writes on unexpected errors** — `view` should call `_log_self(root, msg)` on caught `(OSError, ValueError)` paths, matching the precedent at `cost.py:782`, `ls.py:517`.
4. **Rich-gating boundary** — the gate placement is the most important detail in the spec. Place the `try: from rich... except ImportError: print(hint); return 1` **inside `run_view`, after the `if as_json:` branch checks**. This way:
   - `--json` path never touches rich → works without it.
   - non-JSON path errors cleanly with rc=1 + install hint.
   - importing `agentlog.view` from elsewhere (CLI module load) doesn't fail.

## Impact Analysis

### Scope of Change

**Net new code:** ~400-500 LOC in `view.py` (rendering logic + tool dispatch + JSON mode), ~300-400 LOC in `tests/test_view.py`.

**Edits to existing code:** trivial — ~10 lines in `cli.py`, 1 line in `tests/test_cli_smoke.py`.

**Surface area touched:** zero new dependencies (rich already in `tui` extra), zero new on-disk artifacts (read-only), zero new public-facing constants. The schema is unchanged; this is purely a consumer.

**Risk class:** LOW for the codebase (read-only leaf), MEDIUM for the project (this is the README screenshot — visual quality and tool-use summary correctness are the deliverable, not just "it runs").

### Risks and Considerations

1. **`tool_use.params_summary` is a string, not a dict.** Capture writes it as `json.dumps(...)` (capture.py:214). Tail writes it the same way (tail.py:207). The summarizer MUST `json.loads(params_summary)` first AND tolerate the result being a non-dict (truncated JSON), an empty dict, or missing expected keys. Wrap in `try/except (json.JSONDecodeError, TypeError)` → fall back to the raw `params_summary` string truncated to 60 chars.
2. **ANSI escape sequences in event text are a real attack vector.** A malicious or buggy SDK source file could contain `\x1b[2J` (clear screen) or worse in an `assistant_text` event. Rich's `console.print` with `markup=False` does NOT strip ANSI — it passes raw bytes through. Use `rich.text.Text(s, no_wrap=False)` constructed with `Text.from_ansi` ONLY where you want ANSI rendering, OR strip the escapes before rendering (`re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)`). The spec calls this out explicitly as a required test.
3. **Sort order is meaningful and tested (lesson #1).** Events on disk are append-order, which IS chronological for both capture (write order = real time) and tail (sequential file read). But the spec says "events render in `started_at` ascending" — DO NOT trust file order, sort by `timestamp` explicitly. Write a regression test that seeds two events out of file order and asserts the older one appears above.
4. **Stub references in `cli.py` and tests (lesson #4).** `_STUB_SUBCOMMANDS = frozenset({"view"})` AND the `test_subcommands_registered_but_not_implemented` parametrization both reference `view` as not-yet-implemented. Both must update in the same diff. Grep for `not yet implemented`, `not_implemented`, `_STUB`, `STUB_SUBCOMMANDS` in changed files post-build.
5. **`--limit 0` semantics.** Spec: 0 = unlimited. Match `cli.py:117-121` `ls --limit 0` convention. Do NOT default to "all" silently — explicit 0 from the user only.
6. **Missing-file degradation.** `state.json` missing → rc=2 (per spec — can't render header). `events.jsonl` missing → render header + cost, timeline shows placeholder. `cost.json` missing → render header + timeline, cost section shows placeholder. The behavior tree is non-trivial; structure as three early-load steps with explicit None handling rather than one big try.
7. **`view --json` against a missing run.** Spec: rc=2 + stderr message, NO partial JSON. Verify by ordering — validate run existence BEFORE building any JSON output.
8. **Unknown-model cost rendering.** `_compute_run_cost` returns `cost_usd=None` and `cost_unknown_reason="model not in pricing table"` when the model isn't priced. `view`'s cost footer must show `??` (same convention as `agentlog cost`). The 4-row breakdown should also show `??` per-kind.
9. **Visual quality is non-negotiable.** This is the README screenshot. Pad/align/color choices that look "fine" in dev but ugly in screenshot are a fail. Recommendation: during build, run `agentlog view <real-sdk-run-id>` against the existing `runs/sdk-f50fb891-...` directory referenced in the prompt and eyeball the output BEFORE writing tests. Adjust until it looks like the spec's mockup.
10. **`tool_use` rendering is the most-likely-to-look-bad row.** Tool name padding to 8 chars (`Read    `, `Edit    `, `Grep    `, `Bash    `, `Glob    `) is fine, but `_some_internal_long_tool_name` will blow the alignment. Either truncate tool names at 8 chars + `…` or dynamically size the column. Either is defensible; pick one and test both short and long names.

### Existing Patterns to Follow

- **Module docstring with pinned invariants** — `cost.py:1-20` and `tail.py:1-23` both lead with a numbered list of "invariants for future contributors." `view.py` should do the same.
- **`_data_root()` + `_log_self()` duplication, not sharing** — established by `cost.py:45-60` and `tail.py:52-86`. Don't refactor.
- **Fail-loud user CLI contract** — same as `cost.py:687` / `tail.py:469`: rc=0 success, rc=1 unexpected I/O, rc=2 user error (bad id, missing run, mutual exclusion). Wrap the main body in `try: ... except (OSError, ValueError) as exc: _log_self(...); print(f"agentlog view: error: {exc}", file=sys.stderr); return 1`.
- **Error message format** — `agentlog <subcommand>: error: <detail>` on stderr. Match exactly: `f"agentlog view: error: run id '{run_id}' not found at {run_dir}"`.
- **Rich-import gating** — `ls.py:414-420` shows the soft-fallback pattern (rich missing → fall through to plain text). `view`'s gate is harder (rc=1 + hint) because rich is the whole point of `view`, but the import structure is identical.
- **Dispatch tables (lesson #9)** — `capture._DISPATCH` (capture.py:366-372) and `tail._RECORD_TRANSLATORS` (tail.py:266-273) are the precedents. The `view._TOOL_SUMMARIZERS` table should follow the same shape: `dict[str, Callable[[dict[str, Any]], str]]` with a default-fallback for the missing-key case.
- **Test naming** — `tests/test_cost.py` uses `test_<area>_<scenario>_<observable>` (e.g., `test_cost_all_unknown_model_runs_sort_LAST_not_first`). Lesson #11 mandates this naming for any regression test.
- **JSON output style** — `cost._format_single_json` / `_format_all_json` use `json.dumps(payload, indent=2)`. Match for `view --json`.

## Recommendations

### Build phase ordering (suggested)

1. **First**: write `view.py` with stub renderers — wire the CLI, get `agentlog view <id>` to print "ok" so the harness works end-to-end. Don't worry about formatting yet.
2. **Second**: implement the per-tool dispatch table + summarizer extraction (it's the most-easily-unit-tested piece and the most critical for the "did the agent lie" use case).
3. **Third**: implement the three renderers (header panel, timeline, cost footer) one at a time, eyeballing against a real run dir each time.
4. **Fourth**: implement `--json` mode (mostly composition — call existing cost helpers, dump events.jsonl, dump state.json).
5. **Fifth**: implement the flags (`--limit`, `--events-only`, `--no-truncate`).
6. **Last**: write `tests/test_view.py` covering all acceptance criteria. Include the lesson #1 sort-order regression test from the start (don't defer to polish pass).

### Pre-commit checklist (catches lessons #1, #4, #9)

- [ ] `grep -nE "not[_ ]yet[_ ]implemented|_STUB|will replace|future|TODO|FIXME" src/agentlog/view.py src/agentlog/cli.py tests/test_view.py tests/test_cli_smoke.py` — should return only intentional matches.
- [ ] `frozenset({"view"})` no longer appears in `cli.py`; `_STUB_SUBCOMMANDS` is `frozenset()` (or removed if no other stubs remain).
- [ ] `test_cli_smoke.py:25` exclusion list includes `"view"`.
- [ ] A `test_view_timeline_renders_older_event_above_newer` (or equivalent name) exists.
- [ ] `_TOOL_SUMMARIZERS` exists as `dict[str, Callable]`; a test asserts every key handles a typical input AND the dispatch table's keys match a documented set (lesson #2/#9).
- [ ] ANSI-escape safety test exists and passes (`assistant_text` with `\x1b[2J` does not corrupt output).
- [ ] `view --json` is verified callable without `rich` installed (monkeypatch `sys.modules['rich']` to `None` or use `importlib` machinery in the test).
- [ ] `ruff check src tests && mypy` clean.
- [ ] Run `agentlog view <real-sdk-run-id>` against the existing `runs/sdk-f50fb891-...` and capture the output in `docs/feature-cb153ac3-view-tui.md` (lesson: visual quality verification is part of the build, not just the docs phase).

### Architectural call to NOT make

Do NOT factor a shared `agentlog/_render.py` module out of cost/view helpers to "DRY up" the formatters. The cost and view formatters have different layout intent (cost is a 4-column data table; view is a labeled header card + a tree + a compact subtable). Sharing them would couple two layout decisions that should evolve independently. The duplication-tolerance precedent (cost.py:41 comment) is correct for this codebase at v0.1 scale.

### Reviewer pre-emption

The reviewer is likely to flag:
- "`from rich import ...` inside a function" → pre-emptive comment: `# Gated import: --json mode must work without rich installed.`
- "Why is `_TOOL_SUMMARIZERS` a top-level dict instead of a class?" → pre-emptive comment matching the precedent from `capture._DISPATCH` and `tail._RECORD_TRANSLATORS`.
- "Why does `_data_root()` exist here when it's already in cost.py?" → pre-emptive comment matching tail.py:14 ("inverted failure contract" rationale, or in this case just "consistent with cost.py / ls.py / tail.py — shared `_io.py` deferred to v0.2+").
- Lint complaints about catching broad `Exception` in the rich-gate path → use specific `ImportError` only.
