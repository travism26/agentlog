# Research: docs/getting-started.md + cli-reference.md + architecture.md

## Metadata

adw_id: `408ef453`
prompt: `/tmp/agentlog_docs_prompt.md` — write three new docs pages (getting-started, cli-reference, architecture) for agentlog v0.1, sitting between README.md and DESIGN.md as the technical reference layer.
date: `2026-05-28`

## Executive Summary

The task is doc authoring only — no source edits. The CLI surface (`init`, `uninstall`, `tail`, `ls`, `cost`, `view`, hidden `_hook`) is fully implemented and stable; flag definitions are locked in `src/agentlog/cli.py`, exit-code semantics are uniform (0 / 1 / 2), and the on-disk format (`runs/<id>/{state.json,events.jsonl,cost.json}` + `~/.agentlog/{index.sqlite3,pricing.json,_self.log}`) is fully constrained by `src/agentlog/_constants.py`. The three pages can be written entirely from existing artifacts: `cli.py` + `--help` for the flag tables, `_constants.py` + per-module headers for the schema details, and `DESIGN.md` / `CLAUDE.md` / `docs/adw-lessons.md` for the cross-referenced "why" content (these should be linked, not restated).

## Existing Architecture

### Relevant Documentation Found

| File | Role |
|---|---|
| `README.md` | Front door; elevator pitch, install, 60-second tours (hooks + SDK), `What you get`, `What this is NOT`, 30-second architecture diagram. Hand-written — DO NOT MODIFY. |
| `DESIGN.md` | Locked v0.1 design (480 lines). Problem statement, audience tiers, architecture rationale, hook integration, performance contract, ship scope, explicit non-goals, decisions log, open questions. Source of truth for **why**. |
| `CLAUDE.md` | Project orientation + 8 non-negotiable hard rules (perf budget, fail-open, no auto-install, no kill-switch v0.1, no `PreToolUse` v0.1, local-first, schema versioned, `init` preserves existing hooks). |
| `docs/adw-lessons.md` | 11 polish-pass patterns from the SDLC runs that built v0.1. Lessons #4 (stale future-comments), #5 (durable installed-format strings), #11 (regression-test naming) apply directly to docs. |
| `docs/blog-draft.md` | Untracked launch blog post draft. Different voice/audience — DO NOT MODIFY, DO NOT MATCH its register. |
| `docs/feature-*.md` (6 files) | One published doc per ship-scope item. Mirror filenames in `specs/`. Authoritative narrative for each subcommand. |
| `specs/feature-*.md` (6 files) | Authoritative implementation specs for each ship-scope item. |
| `ai_docs/research/*-analysis.md` (6 files) | Prior per-feature research notes, one per ship-scope item. Useful background but not source of truth. |
| `research/langgraph_patterns_2026.md`, `research/ai_dev_pain_points_2026.md` | Background research that motivates the project. Out of scope for these docs. |

### Component Map

```
User                                    Authority for docs
 │
 ├── reads README.md          ◀── elevator pitch, install
 │     └── click into ────▶ docs/getting-started.md     (5-minute walkthrough)
 │     └── click into ────▶ docs/cli-reference.md       (per-flag matrix)
 │     └── click into ────▶ docs/architecture.md        (extender's view)
 │                              │
 │                              ├── links to DESIGN.md          (decisions, scope)
 │                              ├── links to CLAUDE.md          (hard rules)
 │                              ├── links to docs/adw-lessons.md (contributor patterns)
 │                              └── references src/agentlog/_constants.py (durable format)
```

The three new pages form the **reference layer**: README is the marketing front door, DESIGN.md is the "why" doc, and the new pages are the "how / what / exactly" layer.

### Key Files and Modules

| File | What the docs need from it |
|---|---|
| `src/agentlog/cli.py` | Authoritative source for argparse flag names, types, defaults, help text, subcommand order. |
| `src/agentlog/_constants.py` | Durable installed-format strings: `HOOK_COMMAND_PREFIX="agentlog _hook"`, `EVENTS=(SessionStart, UserPromptSubmit, PostToolUse, Stop, SessionEnd)`, `SCHEMA_VERSION=1`, `INDEX_SCHEMA_VERSION=1`, `SOURCE_HOOKS="hooks"`, `SOURCE_SDK="sdk"`, `MAX_INLINE_BYTES=4096`, `DEFAULT_DATA_ROOT_NAME=".agentlog"`, `RUNS_DIR_NAME="runs"`, `INDEX_FILE_NAME="index.sqlite3"`, `PRICING_FILE_NAME="pricing.json"`, `SELF_LOG_NAME="_self.log"`, `UNKNOWN_SESSION_PREFIX="unknown_session"`. |
| `src/agentlog/hooks_install.py` | `run_init(project, dry_run) -> int`, `run_uninstall(project, dry_run) -> int`. RC 0 / 1 (malformed settings). Idempotent merge via `plan_install` / `plan_uninstall`. Writes `~/.claude/settings.json` (or `./.claude/settings.json` if `--project`) atomically with `sort_keys=True`. `PostToolUse` group gets `matcher: "*"`. |
| `src/agentlog/capture.py` | `dispatch(event, payload)` + `run_hook(event)` fail-open boundary. Five per-event recorders: `_on_session_start`, `_on_user_prompt_submit`, `_on_post_tool_use`, `_on_stop`, `_on_session_end`. Unknown events recorded as `event="unknown"` with raw payload. Top-level `try/except` + nested `contextlib.suppress` around self-logging makes the boundary unconditional return 0. |
| `src/agentlog/tail.py` | `run_tail(path, run_id, source_name, dry_run, force) -> int`. RC 0/1/2. Reads `cc_raw_output.jsonl`, translates stream-json records via `_RECORD_TRANSLATORS` dispatch dict. **Timestamp derivation:** END = file mtime; START = END − `result.duration_ms` (fallback: END − max(1, event_count)s); per-event timestamps linearly interpolated across [start, end] for monotonic display. `--run-id` only valid for single-file ingestion. |
| `src/agentlog/ls.py` | `run_ls(source, since, sort_key, reverse, limit, as_json, reindex) -> int`. RC 0/1/2. **Schema-version bootstrap order** (per lesson #3): (1) `_ensure_schema_version_table` first, (2) check version → drop `runs` on mismatch, (3) recreate `runs` + indexes. mtime-fingerprint refresh: drop missing run rows, upsert stale rows. SQLite index is NEVER source of truth — JSON files are. Also owns `_parse_duration` (e.g., `30m`, `24h`, `7d`) reused by `cost`. |
| `src/agentlog/cost.py` | `run_cost(run_id, all_, source, since, pricing_path, as_json, no_cache_cost) -> int`. RC 0/1/2. **Pricing override chain** (highest to lowest): `--pricing PATH` → `$AGENTLOG_PRICING` env → `$AGENTLOG_HOME/pricing.json` → built-in table (dated 2026-05-27). Merge is per-model whole-row replacement, not field-level. `--no-cache-cost` excludes cache_creation only (NOT cache_read). |
| `src/agentlog/view.py` | `run_view(run_id, limit, events_only, no_truncate, as_json) -> int`. RC 0/1 (1 = missing `rich`)/2. Three-panel layout: header (`rich.box.HEAVY`), timeline (ASCII rail `─ ┌ │ └`, color-coded by event kind), cost footer. `--json` bypasses `rich` entirely. `[tui]` extra (i.e., `rich`) required for non-JSON output. |

### Environment Variables

- `AGENTLOG_HOME` — overrides the data root (default `~/.agentlog`). Honored by all read/write paths.
- `AGENTLOG_PRICING` — overrides the pricing-table path (between `--pricing` and `$AGENTLOG_HOME/pricing.json` in the chain).

## Affected Areas

### Files That Will Need Changes

| File | Status | What changes |
|---|---|---|
| `docs/getting-started.md` | NEW | Create. ~150–250 lines. Linear walkthrough: install → init → claude session → ls → cost → view → uninstall → "where things live" table → next-steps link block. |
| `docs/cli-reference.md` | NEW | Create. ~300–500 lines. One section per subcommand in `--help` order (`init`, `uninstall`, `tail`, `ls`, `cost`, `view`), plus a brief `_hook` note. Each section: Synopsis / Description / Flags table / Exit codes / Examples. |
| `docs/architecture.md` | NEW | Create. ~200–350 lines. Eight sections per prompt: two-paths-one-schema diagram → `runs/<id>/` layout table → hook handlers (with perf contract + PreToolUse absence rationale) → tail translator (timestamp derivation paragraph is folklore) → SQLite index (refresh-on-stale + bootstrap order) → pricing table (four-level override) → out-of-scope recap → contributor pointer to `docs/adw-lessons.md` + `.adw/travis/travis_sdlc.py`. |

### Dependencies

- The new pages depend on (read-only): `cli.py`, `_constants.py`, `DESIGN.md`, `CLAUDE.md`, `docs/adw-lessons.md`, `README.md`, per-feature spec files.
- README.md already advertises the three new files (`docs/cli-reference.md` is linked at line 157, `docs/adw-lessons.md` at line 145). Creating them resolves a dangling link.
- No source code changes are required or appropriate.

### Integration Points

- README → docs cross-links (already in place).
- Internal cross-links between the three new pages (`getting-started.md` → `cli-reference.md` + `architecture.md`, and vice versa).
- Outbound links to `DESIGN.md`, `CLAUDE.md`, `docs/adw-lessons.md`, and the future GitHub issue tracker URL.

## Impact Analysis

### Scope of Change

Pure documentation work, three new files. No code, no tests, no migrations. Zero runtime risk. The only verification surface is:

1. Markdown well-formedness.
2. Flag tables matching live `agentlog <subcommand> --help` output.
3. No leakage of sanitized bb-token strings.
4. No `(v0.2+)` feature described as present-tense reality.
5. No duplication of `README.md` or `DESIGN.md` content (cross-link instead).

### Risks and Considerations

- **Drift risk (highest):** the CLI reference's flag tables can desync from `cli.py` on future flag additions. Mitigation: the prompt mandates running `agentlog <name> --help` for each section as the canonical source at write time. A future-facing alternative (out of scope here) would be a `--help`-to-Markdown generator, but that is over-engineering for a 6-subcommand v0.1.
- **Tone drift:** the existing `docs/blog-draft.md` is a longer-form dev.to-style artifact. The prompt is explicit that the new pages should match `CLAUDE.md` / `DESIGN.md` register (terse, factual, low buzzword count), NOT the blog draft.
- **bb-token leakage** (CLAUDE.md "Code provenance" section): the sanitization checklist forbids any reference to bug bounty, HackerOne, recon, exploit, deepener, nuclei, burp, caido. The prompt mandates a final `grep -rinE` pass before declaring done.
- **Lesson #4 (stale future-comments):** v0.1 ship-scope items 1–6 ARE done. Any wording like "will eventually" applied to those features is a bug. Use present tense for shipped items and explicit `(v0.2+)` or `(roadmap)` markers for deferred ones.
- **Lesson #5 (durable installed-format strings):** `HOOK_COMMAND_PREFIX`, `SCHEMA_VERSION`, `INDEX_SCHEMA_VERSION`, `SOURCE_HOOKS`, `SOURCE_SDK` are all user-visible (they end up in `~/.claude/settings.json`, JSONL records, SQLite rows). Treat them as part of the documented contract — when the architecture page names them, the names are load-bearing, not casual references.
- **Lesson #11 (regression-test naming):** the architecture page's "For contributors" section can reference real tests by name. They exist under `tests/test_{capture,cli_smoke,cost,handler_perf,hooks_install,ls,tail,view}.py`.
- **PreToolUse absence:** must be explained in `architecture.md` per the prompt, citing CLAUDE.md hard rule #5. Do not silently omit — the absence IS a design decision and readers ask about it.

### Existing Patterns to Follow

- **Tone match:** terse, factual, no marketing hype. Compare CLAUDE.md / DESIGN.md / `docs/feature-*.md` for register.
- **Tables for reference data:** `DESIGN.md` and `CLAUDE.md` both use tables aggressively for flag / scope / audience matrices. The CLI reference page should follow the same convention.
- **ASCII boxes for diagrams:** README.md and DESIGN.md both use plain ASCII box drawings (`┌─┐`, `│`, `└─┘`) for architecture diagrams. The architecture page should match (no Mermaid, no PlantUML).
- **Cross-link headings via `#section-slug`:** standard GitHub-flavored Markdown anchor generation.
- **Code block conventions:** `bash` for shell, `python` for Python, no language tag for output samples (README precedent).
- **One-line description per command:** the README's "CLI reference" table at line 149 establishes the one-liner phrasing that the cli-reference page should expand on.

## Recommendations

1. **Write in this order:** (1) cli-reference.md (most mechanical, anchors the flag contract), (2) architecture.md (depends on knowing what each CLI surface does), (3) getting-started.md (depends on both — links forward to them). This minimizes the chance of cross-link breakage.

2. **For each cli-reference section, run `agentlog <name> --help` first** and paste the output into your scratch buffer before writing the table. This avoids the lesson #4 / lesson #5 class of drift bugs.

3. **In architecture.md, the timestamp-derivation paragraph (section 4) should be 4–6 sentences** — it is genuinely subtle (file mtime as end, back-derived start from `result.duration_ms`, linear interpolation), and the prompt explicitly calls it out as "useful design folklore" worth preserving. Reference `_RECORD_TRANSLATORS` by name (lesson #5: it is a real identifier worth keeping stable).

4. **Pre-commit to the test shapes from adw-lessons.md** when describing what the codebase verifies:
   - Lesson #1: any sort ordering claim in architecture.md should reference the named `tests/test_ls.py` / `tests/test_cost.py` regression test that proves it. (Lesson #11.)
   - Lesson #3: when describing the SQLite bootstrap order, name the test that proves the recovery path (seed bad version → run → assert clean rebuild).
   - Lesson #7: when describing the fail-open boundary in `capture.run_hook`, note that even `_log_self` is wrapped in `contextlib.suppress` so the boundary's exit path is unconditional `return 0`.

5. **For the `runs/<id>/` layout table in architecture.md**, the fields to cover (from `_constants.py` + per-module headers): `state.json` (schema_version, session_id, parent_session_id, started_at, ended_at, cwd, model, event_count, source, summary), `events.jsonl` (one JSON record per line; each carries schema_version, event, timestamp, session_id, source, plus event-specific fields), `cost.json` (schema_version, session_id, totals = {input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}, phases), `_logs/` (reserved, not yet written to in v0.1), `~/.agentlog/_self.log` (fail-open landing zone — append-only, timestamped). Also `~/.agentlog/index.sqlite3` (SQLite index — `runs` table + `schema_version` table; lesson #3 ordering) and `~/.agentlog/pricing.json` (optional user pricing override).

6. **Cross-link, don't restate, three specific times:**
   - architecture.md "What's deliberately out of scope" should link to DESIGN.md's "Explicit non-goals for v0.1" rather than restate the list.
   - architecture.md "Performance contract" should link to CLAUDE.md hard rule #1 / #2 rather than restate the budget table.
   - architecture.md "For contributors" should link to `docs/adw-lessons.md` for the recurring-pattern catalog.

7. **Verification checklist before declaring done:**
   - `grep -rinE "bug.bounty|hackerone|recon|exploit|deepener|nuclei|burp|caido" docs/` returns empty.
   - Each cli-reference flag table matches `agentlog <name> --help` flag-for-flag (no invented flags, no missing flags).
   - No `(v0.2+)` feature described in present tense; no shipped feature described as future.
   - Every internal anchor link (`docs/architecture.md#schema`) resolves to a real `## Schema` heading.
   - `README.md` and `docs/blog-draft.md` are untouched.

8. **`_hook` subparser handling:** it is hidden from `--help` (cli.py:220–225 explicitly suppresses it). The cli-reference page should mention its existence in a small note under either the `init` or `uninstall` section (or at the page bottom), explain it is the routing target for installed hook commands, and say it should not be invoked manually. Do NOT promote it to a top-level section — it is not a user command.
