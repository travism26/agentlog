# Feature: docs/getting-started.md + cli-reference.md + architecture.md

## Metadata

adw_id: `408ef453`
prompt: `/tmp/agentlog_docs_prompt.md` — write three new docs pages (getting-started, cli-reference, architecture) for agentlog v0.1, sitting between README.md and DESIGN.md as the technical reference layer.

## Feature Description

Add three Markdown reference pages under `docs/`:

1. `docs/getting-started.md` — a 5-minute, linear walkthrough from `pip install` to "I can see what my agents cost." Hooks mode + SDK mode, both ingest paths covered.
2. `docs/cli-reference.md` — one per-subcommand section (init, uninstall, tail, ls, cost, view) plus a small `_hook` note. Synopsis / Description / Flags / Exit codes / Examples.
3. `docs/architecture.md` — how-it-works-under-the-hood page covering the two ingest paths, the `runs/<id>/` layout, hook handlers (with perf contract and PreToolUse absence rationale), the tail translator's timestamp derivation, the SQLite index, the pricing table, deliberate non-goals, and a contributor pointer.

These pages form the technical reference layer between the marketing front door (`README.md`) and the locked design doc (`DESIGN.md`). They cross-link to DESIGN.md and CLAUDE.md for the "why," and reference `src/agentlog/_constants.py` for durable installed-format strings. No source code, no tests — pure documentation.

## User Story

As a developer arriving at the agentlog repo from the README
I want to click into a depth page that tells me, without marketing fluff, exactly how to use a CLI subcommand, what's on disk, and where each piece of state lives
So that I can adopt agentlog confidently, extend it for my own needs, or audit the design choices before trusting it in my Claude Code workflow.

## Problem Statement

The v0.1 ship-scope (items 1–6) is implemented and committed, but `README.md` is the only user-facing doc — and it deliberately stays terse. There is no destination for a reader who clicks past the elevator pitch:

- The "CLI reference" table in `README.md` (line 149) is a one-liner-per-command summary; there is nowhere to look up exit codes, flag defaults, or per-flag behavior.
- `DESIGN.md` documents *why* decisions were made, not *how* the shipped code works at field-name granularity. A reader who wants to inspect `runs/<id>/state.json` has to read source.
- `README.md` already links to `docs/cli-reference.md` (line 157) — the link is currently dangling.

Without a reference layer, the project misses two opportunities: (a) the career-signal/portfolio audience can't see the depth, only the pitch; (b) any future contributor has to re-derive the on-disk format and the perf contract from source each time.

## Solution Statement

Write three new Markdown files that read as factual reference, not marketing copy. Match the register of `CLAUDE.md` and `DESIGN.md` (terse, tables-heavy, ASCII diagrams). For each new page:

- Cross-link to `DESIGN.md` / `CLAUDE.md` / `docs/adw-lessons.md` instead of restating their content.
- Source flag tables from the live `agentlog <name> --help` output (run the binary, paste the output, then format).
- Source on-disk field names from `src/agentlog/_constants.py` so the documented names match the durable installed format (ADW lesson #5).
- Stay strictly within v0.1 reality. Anything deferred gets an explicit `(v0.2+)` or `(roadmap)` marker.

No source code or test changes. Verification is a small set of greps (bb-token leakage, present-tense future features) plus a manual flag-table cross-check.

## Relevant Files

Use these files to implement the feature:

- `README.md` — front door; reference for tone, install commands, the 30-second architecture diagram, and the existing `docs/cli-reference.md` link target (line 157). **Read-only — do not modify.**
- `DESIGN.md` — locked v0.1 design. Source of *why* decisions for cross-linking. Sections to link: "Explicit non-goals for v0.1", "Performance contract", "Hook integration model".
- `CLAUDE.md` — non-negotiable hard rules. The architecture page's "Hook handlers" section links to hard rule #1 / #2 (perf budget, fail-open) and hard rule #5 (PreToolUse deferred past v0.1).
- `docs/adw-lessons.md` — recurring polish patterns. The architecture page's "For contributors" section links here. Read BEFORE drafting (per `/feature` instructions); lessons #3 (SQLite bootstrap order), #4 (stale future-comments in docs), #5 (durable installed-format strings), #7 (fail-open wraps its own logging), #11 (named regression tests) all apply.
- `docs/blog-draft.md` — launch blog post draft. **Read-only — do not modify, do not match its register.** Different voice/audience.
- `docs/feature-*.md` (6 published feature docs) and `specs/feature-*.md` (6 implementation specs) — authoritative narrative source per ship-scope item. Read selectively when writing the corresponding cli-reference section.
- `ai_docs/research/408ef453-docs-pages-research.md` — pre-planning research for this spec. Captures the component map, the per-module reference points, and the verification checklist. Cross-referenced throughout.
- `ai_docs/research/{0241d756,07ec0bb6,1b4319ab,355ec9b6,cb153ac3,fabf1d0d}-*.md` — prior per-feature research notes. Useful background; not source of truth.
- `src/agentlog/cli.py` — argparse definitions for `init`, `uninstall`, `tail`, `ls`, `cost`, `view`, and the hidden `_hook` subparser (suppressed from `--help`). Source of truth for flag names, types, defaults.
- `src/agentlog/_constants.py` — durable installed-format strings. Architecture page names these explicitly: `HOOK_COMMAND_PREFIX`, `EVENTS`, `SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`, `SOURCE_HOOKS`, `SOURCE_SDK`, `MAX_INLINE_BYTES`, `DEFAULT_DATA_ROOT_NAME`, `RUNS_DIR_NAME`, `INDEX_FILE_NAME`, `PRICING_FILE_NAME`, `SELF_LOG_NAME`, `UNKNOWN_SESSION_PREFIX`.
- `src/agentlog/hooks_install.py` — `run_init` / `run_uninstall`; idempotent merge logic; settings.json side effects with `sort_keys=True` (ADW lesson #6).
- `src/agentlog/capture.py` — `dispatch` + `run_hook` fail-open boundary; per-event recorder functions; unknown-event fallback.
- `src/agentlog/tail.py` — `run_tail`; `_RECORD_TRANSLATORS` dispatch dict; timestamp-derivation strategy (file mtime as end, back-derive start from `result.duration_ms`, linear interpolate per-event).
- `src/agentlog/ls.py` — `run_ls`; SQLite bootstrap order (lesson #3); `_parse_duration`.
- `src/agentlog/cost.py` — `run_cost`; four-level pricing override chain.
- `src/agentlog/view.py` — `run_view`; three-panel layout; gated `rich` import; `[tui]` extra.
- `tests/test_{capture,cli_smoke,cost,handler_perf,hooks_install,ls,tail,view}.py` — real test names that the architecture page's "For contributors" section can reference (lesson #11).

### New Files

- `docs/getting-started.md` — 150–250 lines. Linear walkthrough.
- `docs/cli-reference.md` — 300–500 lines. Per-subcommand reference.
- `docs/architecture.md` — 200–350 lines. Under-the-hood reference.

## Implementation Plan

### Phase 1: Foundation

Verify the documentation environment and lock the contract surfaces that the three pages will reference:

- Confirm `agentlog --help` and each subcommand's `--help` run cleanly from the installed `.venv` binary (drift-check before writing).
- Re-read `README.md` end-to-end to identify every dangling internal link the new pages should resolve (currently `docs/cli-reference.md` at line 157).
- Re-read `DESIGN.md` "Explicit non-goals for v0.1" and "Performance contract" sections so the architecture page can link to specific anchors rather than restate.
- Re-read `docs/adw-lessons.md` (already done during research) — confirm which lessons apply to docs work (#4, #5, #11 are the prime ones).
- Decide page-order of writing (per research recommendation): cli-reference first (anchors the flag contract), architecture second (depends on knowing the CLI surface), getting-started last (links forward to both).

### Phase 2: Core Implementation

Write the three pages in order. For each page, follow this loop:

1. Open the relevant source files / `--help` output / spec docs in scratch.
2. Draft the page top-to-bottom against the prompt's section list.
3. Run the verification greps from `## Compile Checks` against the new file.
4. Cross-check internal anchor links by searching for the target heading in the corresponding file.

**Page 1 — `docs/cli-reference.md`:**

- One section per subcommand in `--help` order: `init`, `uninstall`, `tail`, `ls`, `cost`, `view`.
- Each section: Synopsis / Description / Flags table / Exit codes / Examples (3–6 each).
- Brief `_hook` note (do not promote to top-level section — it is hidden in `cli.py:220–225`).
- Flag tables sourced from live `agentlog <name> --help`; exit codes sourced from each module's `run_*` function.
- Cross-link `view` → `cost` (cost computation), `ls` → `cost` (sort key reuse), `tail` → `architecture.md#tail-translator` (timestamp derivation).

**Page 2 — `docs/architecture.md`:**

- Eight sections per prompt:
  1. Two ingest paths, one schema — ASCII diagram, framing paragraph.
  2. The `runs/<id>/` directory layout — full table. Reference `_constants.py` for field names.
  3. The hook handlers — five v0.1 events; PreToolUse absence rationale citing CLAUDE.md hard rule #5; perf contract citing hard rules #1 and #2.
  4. The tail translator — `_RECORD_TRANSLATORS` dispatch dict; 4–6-sentence timestamp-derivation paragraph (file mtime as end, back-derived start from `result.duration_ms`, linear interpolation).
  5. The SQLite index — refresh-on-stale algorithm; schema-version bootstrap order (lesson #3); explicit "not the source of truth — JSON files are."
  6. The pricing table — four-level override chain (`--pricing` flag > `$AGENTLOG_PRICING` env > `$AGENTLOG_HOME/pricing.json` > builtin); merge semantics (per-model whole-row replacement); staleness reminder.
  7. What's deliberately out of scope — link to DESIGN.md's "Explicit non-goals for v0.1," do not restate.
  8. For contributors — link to `docs/adw-lessons.md`; note that any new feature should follow the same SDLC pipeline via `.adw/travis/travis_sdlc.py`. Reference real test names per lesson #11.

**Page 3 — `docs/getting-started.md`:**

- Seven sections per prompt:
  1. Install (`pip install 'agentlog[tui]'` and `uv tool install 'agentlog[tui]'`; note `[tui]` extra is only for `view`).
  2. Capture interactive sessions (hooks mode) — `agentlog init` + a normal `claude` session + `agentlog ls`. Show expected output.
  3. Capture scripted runs (SDK mode) — `agentlog tail <path>` with a directory walk example.
  4. See what you spent — `agentlog cost <id>` and `agentlog cost --all`; pricing-table caveat with `--pricing` and `$AGENTLOG_PRICING`.
  5. See what they did — `agentlog view <id>` three-panel layout; reference the README hero shot, do not repeat it.
  6. Uninstalling — `agentlog uninstall` preserves user-added hooks; one-line reassurance.
  7. Where things live — table mapping `~/.agentlog/runs/<id>/{state.json, events.jsonl, cost.json}` → contents. Pointer to architecture.md for depth.
- "Next steps" footer linking to cli-reference.md, architecture.md, DESIGN.md, GitHub issue tracker (placeholder URL `https://github.com/travism26/agentlog/issues` per CLAUDE.md "Status").

### Phase 3: Integration

- Re-verify every internal anchor link resolves to a real heading in its target file.
- Run the bb-token grep across `docs/` (per prompt's "Tests / verification" section).
- Cross-check each cli-reference flag table against live `agentlog <name> --help` output one final time.
- Confirm `README.md` and `docs/blog-draft.md` were not modified.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Pre-write verification

- Run `.venv/bin/agentlog --help` and `.venv/bin/agentlog <subcommand> --help` for each of `init`, `uninstall`, `tail`, `ls`, `cost`, `view`. Save output to a scratch buffer.
- Confirm `docs/blog-draft.md` and `README.md` modification times before starting; verify they stay unchanged at the end.
- Re-read `docs/adw-lessons.md` (lessons #4, #5, #11 explicitly).

### 2. Write `docs/cli-reference.md`

- Title + 2–3-paragraph intro framing this as the per-flag-and-exit-code reference; link back to `README.md` for the elevator pitch and forward to `architecture.md` for schema depth.
- Section per subcommand in `--help` order:
  - `## agentlog init` — Synopsis, Description (idempotent merge, dry-run, `--project` scope), Flags table (`--dry-run`, `--project`, `--data-root`), Exit codes (0 ok / 1 malformed settings / 2 usage), Examples (default install, project-scoped, dry-run preview).
  - `## agentlog uninstall` — same structure. Note `init` / `uninstall` are inverses and preservation contract (user-added hooks survive — name `HOOK_COMMAND_PREFIX` as the discriminator per lesson #5).
  - `## agentlog tail` — Synopsis with `<path>`. Description includes `--run-id` single-file constraint. Examples cover single-file ingest, recursive directory walk, `--force` reindex, `--dry-run`.
  - `## agentlog ls` — Flags (`--source`, `--since`, `--sort`, `--reverse`, `--limit`, `--json`, `--reindex`). Note `--since` accepts the same duration grammar (`30m`, `24h`, `7d`) as `cost --since`. Cross-link to architecture.md SQLite section.
  - `## agentlog cost` — Flags (`<run_id>` positional, `--all`, `--source`, `--since`, `--pricing`, `--json`, `--no-cache-cost`). Document the four-level pricing override chain inline (the canonical home is architecture.md; restate the chain order here as user-facing flag behavior). Note `--no-cache-cost` excludes `cache_creation` only, not `cache_read`.
  - `## agentlog view` — Flags (`<run_id>` positional, `--limit`, `--events-only`, `--no-truncate`, `--json`). Note `[tui]` extra required for non-JSON output; `--json` works without `rich`. Cross-link to `cost` for cost-footer math.
- Closing note on the hidden `_hook` subparser: 2–3 sentences explaining it is the routing target for installed hook commands written into `~/.claude/settings.json`, suppressed from `--help` (`cli.py:220–225`), and should not be invoked manually.

### 3. Write `docs/architecture.md`

- Section 1: "Two ingest paths, one schema." Reuse the README architecture diagram in slightly expanded form (label hook event names along the top arrow, label `cc_raw_output.jsonl` → `_RECORD_TRANSLATORS` along the bottom arrow). One paragraph on why a unified schema across both sources is the load-bearing v0.1 decision (reference DESIGN.md's architecture rationale).
- Section 2: "The `runs/<id>/` directory layout." Full table — one row per artifact:
  - `state.json` — `schema_version`, `session_id`, `parent_session_id`, `started_at`, `ended_at`, `cwd`, `model`, `event_count`, `source`, `summary`. Written by `capture._on_session_start` (initial) and `capture._on_session_end` (final).
  - `events.jsonl` — one JSON record per line; each carries `schema_version`, `event`, `timestamp`, `session_id`, `source`, plus event-specific fields. Append-only.
  - `cost.json` — `schema_version`, `session_id`, `totals` (input/output/cache_read/cache_creation tokens), `phases`. Written incrementally by `capture._on_post_tool_use` and finalized on `_on_session_end`.
  - `_logs/` — reserved for v0.1; not yet written.
- Add two supplementary rows for the global artifacts:
  - `~/.agentlog/_self.log` — fail-open landing zone; append-only timestamped.
  - `~/.agentlog/index.sqlite3` — SQLite index for `ls` queries. NOT the source of truth.
  - `~/.agentlog/pricing.json` — optional user pricing override.
- Section 3: "The hook handlers." Five v0.1 events (SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd). What each writes, in one row each. Call out `PreToolUse` absence with a quoted reference to CLAUDE.md hard rule #5 and the rationale ("blocking hooks are the highest-risk surface; earn trust with logging first"). Performance contract: cite CLAUDE.md hard rule #1 (<10ms steady-state, <50ms cold-start, no network calls in hooks) and hard rule #2 (fail-open always). Note lesson #7 — `_log_self` is wrapped in `contextlib.suppress` so the boundary's exit is unconditional `return 0`.
- Section 4: "The tail translator." Reference `_RECORD_TRANSLATORS` dispatch dict by name (lesson #5: it is a real identifier worth keeping stable). 4–6-sentence paragraph on timestamp derivation: file mtime as END, START = END − `result.duration_ms` (fallback: END − max(1, event_count)s if duration missing), per-event timestamps linearly interpolated across [start, end] for monotonic display. Note `--run-id` only valid for single-file ingestion.
- Section 5: "The SQLite index." State plainly: NOT the source of truth (the JSON files are; the index is a derived cache for `ls`-style queries). Refresh-on-stale algorithm: mtime fingerprint per run, drop missing run rows, upsert stale rows. Schema-version bootstrap order (lesson #3): (1) `_ensure_schema_version_table` first, (2) check version → drop incompatible `runs` table on mismatch, (3) recreate `runs` + indexes. Reference the test that proves recovery: `tests/test_ls.py::test_ls_recovers_from_incompatible_index_version` (or the closest real test name from `grep -n "schema_version" tests/test_ls.py`).
- Section 6: "The pricing table." Four-level override chain (highest to lowest): `--pricing PATH` → `$AGENTLOG_PRICING` env → `$AGENTLOG_HOME/pricing.json` → built-in table (snapshot dated 2026-05-27). Merge semantics: per-model whole-row replacement, NOT field-level merge — missing models inherit from built-in. Loud reminder that the snapshot will go stale and the operator owns refreshing it.
- Section 7: "What's deliberately out of scope." Link to DESIGN.md's "Explicit non-goals for v0.1" section anchor. List the items inline as a one-line-each table (no `PreToolUse` hook in v0.1, no cost-budget kill-switch in v0.1, no native Python API in v0.1, no subprocess wrapper in v0.1, no web dashboard ever — local-first principle). Short, factual.
- Section 8: "For contributors." 2–3 paragraphs. Pointer to `docs/adw-lessons.md` for the recurring-pattern catalog. Note that any new feature should follow the same SDLC pipeline: `.adw/travis/travis_sdlc.py` against a spec prompt. Reference real test file names (`tests/test_{capture,cli_smoke,cost,handler_perf,hooks_install,ls,tail,view}.py`) so the reader knows where coverage lives (lesson #11).

### 4. Write `docs/getting-started.md`

- Title + one-paragraph intro: "If you've read the README and want a 5-minute hands-on tour, this is the page."
- Section 1: "Install." Two code blocks — `pip install 'agentlog[tui]'` and `uv tool install 'agentlog[tui]'`. One-sentence note on the `[tui]` extra (only `view` needs `rich`; everything else is stdlib-only).
- Section 2: "Capture interactive sessions (hooks mode)." Sub-steps:
  - `agentlog init` — explain what changes in `~/.claude/settings.json` (registers five hook commands with the `HOOK_COMMAND_PREFIX` discriminator; preserves existing entries; sort-keys the file per lesson #6).
  - Run a normal `claude` session.
  - `agentlog ls` to verify capture — show expected output (run ID, source=hooks, model, started, events).
- Section 3: "Capture scripted runs (SDK mode)." Sub-steps:
  - If you already have `cc_raw_output.jsonl` files from a `claude-code-sdk` or Anthropic SDK run, `agentlog tail <path>` ingests them into the same `runs/<id>/` layout.
  - Show a directory walk: `agentlog tail ./logs/` recursing through nested SDK output. Mention `--run-id` for single-file overrides and `--force` for reindex.
- Section 4: "See what you spent." `agentlog cost <id>` (per-phase + total) and `agentlog cost --all` (every run sorted by cost descending). Pricing-table caveat: built-in snapshot is current as of 2026-05-27; override with `--pricing` or `$AGENTLOG_PRICING`. Link to cli-reference.md `cost` section and architecture.md "The pricing table."
- Section 5: "See what they did." `agentlog view <id>`. Describe the three-panel layout (header / timeline / cost footer); reference the README hero screenshot rather than reproducing it. Mention `--events-only`, `--limit`, `--no-truncate`, `--json` briefly with pointer to cli-reference.md.
- Section 6: "Uninstalling." One paragraph: `agentlog uninstall` removes only the agentlog-tagged hooks (identified via `HOOK_COMMAND_PREFIX`); user-added hooks are preserved.
- Section 7: "Where things live." Table mapping `~/.agentlog/runs/<id>/{state.json, events.jsonl, cost.json}` → one-line contents description. Brief enough that a curious user can `grep`. Pointer to architecture.md for full schema details.
- "Next steps" footer: bulleted links to `docs/cli-reference.md`, `docs/architecture.md`, `DESIGN.md` (ship-scope section), the GitHub issue tracker URL.

### 5. Post-write verification

- Run the bb-token grep: `grep -rinE "bug.bounty|hackerone|recon|exploit|deepener|nuclei|burp|caido" docs/` — must return empty across all three new files.
- For each cli-reference flag table, cross-check against live `agentlog <subcommand> --help` output flag-by-flag.
- Grep each new file for `(future|will replace|TODO|FIXME|XXX|will land in|deferred to)` (lesson #4); confirm each match is correctly marked `(v0.2+)` / `(roadmap)` / cites a real deferred decision.
- Verify every `[link](docs/architecture.md#anchor)` resolves: extract anchors from `docs/architecture.md` headings and confirm referenced anchors exist.
- Confirm `README.md` and `docs/blog-draft.md` modification times are unchanged.

### 6. Markdown well-formedness

- Open each file in a Markdown previewer (or run a Markdown linter if installed) to confirm rendering.
- Confirm table column counts match across header / separator / data rows.
- Confirm code fences are balanced (each opening ``` has a closing ```).

## Testing Strategy

**IMPORTANT:** This feature is documentation-only. There are no pytest tests, no integration tests, no test files to add. The verification surface is small and entirely textual.

Documentation files do not need conventional unit/integration tests. The validation gates are:

### Unit Tests

None. This feature ships no Python code.

### Integration Tests

None. The implicit "integration test" is the human reading the page top-to-bottom and verifying the documented commands and outputs match reality. The `## Step by Step Tasks` section #5 ("Post-write verification") is the closest analog.

### Edge Cases

Verification gates applied at write time (treat each as a pass/fail check, not a pytest):

- **bb-token leakage** (CLAUDE.md "Code provenance" sanitization): `grep -rinE "bug.bounty|hackerone|recon|exploit|deepener|nuclei|burp|caido" docs/` must return empty across all three new files.
- **Flag-table drift** (highest-likelihood doc bug): each `## agentlog <name>` section's flag table must match the live `agentlog <name> --help` output flag-by-flag. No invented flags, no missing flags, no wrong defaults.
- **Stale-future-comment drift** (ADW lesson #4): no v0.1-shipped feature described in future tense; no `(v0.2+)` feature described as present-tense reality.
- **Durable installed-format strings** (ADW lesson #5): `HOOK_COMMAND_PREFIX`, `SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`, `SOURCE_HOOKS`, `SOURCE_SDK` named in architecture.md are load-bearing — they must match `src/agentlog/_constants.py` exactly (case-sensitive). Drift here is a documented-contract violation, not a typo.
- **Anchor link integrity:** every internal `[...](docs/<page>.md#anchor)` must resolve to an actual heading. Manual cross-check.
- **Untouched files:** `README.md` and `docs/blog-draft.md` modification times unchanged at the end of the run.

For the architecture page's references to ADW lessons #1 (sort ordering), #3 (SQLite bootstrap), #7 (fail-open wraps own logging), and #11 (named regression tests), copy the lesson's "Test shape" block by reference (cite the lesson number — do not inline the code snippet, since these tests already exist in the repo and the architecture page is documenting *that they exist*, not asking the reader to write them).

## Acceptance Criteria

- `docs/getting-started.md`, `docs/cli-reference.md`, `docs/architecture.md` all exist and are well-formed Markdown.
- Each new file is within its target line range (getting-started 150–250, cli-reference 300–500, architecture 200–350) — generous tolerance is fine; this is a sizing guideline, not a hard limit.
- Each `## agentlog <name>` section in `cli-reference.md` matches the live `agentlog <name> --help` output flag-for-flag (name, type, default).
- Each subcommand section in `cli-reference.md` includes Synopsis / Description / Flags / Exit codes / Examples — all five subsections present.
- `architecture.md` explicitly explains the `PreToolUse` absence, citing CLAUDE.md hard rule #5.
- `architecture.md`'s timestamp-derivation paragraph (section 4) is 4–6 sentences and names `_RECORD_TRANSLATORS` by name.
- `architecture.md`'s "What's deliberately out of scope" section links to DESIGN.md's "Explicit non-goals for v0.1" rather than restating the list.
- `architecture.md`'s "For contributors" section links to `docs/adw-lessons.md`.
- `getting-started.md` references the README hero screenshot for the `view` section rather than reproducing it.
- `getting-started.md` ends with a "Next steps" section linking to `cli-reference.md`, `architecture.md`, `DESIGN.md`, and the GitHub issue tracker.
- The bb-token grep returns empty across all three new files.
- `README.md` and `docs/blog-draft.md` are unchanged.
- No v0.1-shipped feature described in future tense; no deferred feature described as shipping.
- All `[link](docs/<page>.md#anchor)` references resolve to a real heading.

## Compile Checks

Fast checks to verify the docs are well-formed and the source they reference is still intact. No pytest, no linters, no pipeline runs.

- `ls docs/getting-started.md docs/cli-reference.md docs/architecture.md` — Verify the three new files exist.
- `.venv/bin/python -m py_compile src/agentlog/cli.py && echo "OK"` — Verify the CLI module the docs reference still parses cleanly.
- `.venv/bin/python -c "from agentlog import cli; print('import OK')"` — Verify the module the docs reference still imports cleanly.
- `.venv/bin/agentlog --help` — Verify the top-level CLI still works; output should list all six subcommands referenced in cli-reference.md.
- `.venv/bin/agentlog init --help && .venv/bin/agentlog uninstall --help && .venv/bin/agentlog tail --help && .venv/bin/agentlog ls --help && .venv/bin/agentlog cost --help && .venv/bin/agentlog view --help` — Verify every subcommand --help output still matches what the docs claim.
- `grep -rinE "bug.bounty|hackerone|recon|exploit|deepener|nuclei|burp|caido" docs/` — Must return empty (no bb-token leakage; CLAUDE.md "Code provenance" sanitization gate).
- `grep -nE "v0\.2\+|roadmap" docs/getting-started.md docs/cli-reference.md docs/architecture.md` — Surfaces every deferred-feature reference for a manual present-tense audit.
- `git status --short README.md docs/blog-draft.md` — Verify both untouched.

## Notes

- **No new runtime dependencies.** Docs are static Markdown; no `uv add` needed.
- **No code changes.** `src/agentlog/*` and `tests/*` stay untouched. Source files are read-only references in this work.
- **Voice contract:** match the register of CLAUDE.md and DESIGN.md (terse, factual, table-heavy), NOT the longer-form dev.to voice of `docs/blog-draft.md`. The blog draft is a separate artifact for a separate audience.
- **Write order:** cli-reference first (anchors the flag contract), architecture second (depends on knowing what each CLI surface does), getting-started last (links forward to both). Minimizes cross-link breakage.
- **GitHub URL placeholder:** the project is not yet on GitHub per CLAUDE.md "Status." Use `https://github.com/travism26/agentlog/issues` for the issue-tracker link target — this is the planned URL per CLAUDE.md. When the repo is published, the link resolves; until then, it's a forward-compatible placeholder.
- **Privacy implications:** these are reference docs for a local-first observability tool. No new network/export surface is introduced. The pricing-table override chain documented in `architecture.md` (section 6) is the only environment-variable surface mentioned — read-only, no exfil.
- **Drift mitigation (out of scope for v0.1):** a future enhancement would generate the cli-reference flag tables from `--help` output via a script. For a 6-subcommand surface, manual upkeep is cheaper than the script. Re-evaluate when the surface grows past ~12 subcommands.
- **Per `/feature` instructions:** every `<placeholder>` in the prompt's Plan Format has been replaced. No additional placeholders remain in this spec.
