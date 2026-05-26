# CLAUDE.md — agentlog Project Orientation

**agentlog** — local-first observability for AI coding agents. Captures every Claude Code session (interactive AND scripted SDK runs) into one unified, replayable log.

**Status**: pre-implementation. v0.1 design fully locked 2026-05-26. Repo not yet created on GitHub (planned: `github.com/travism26/agentlog`).

## Where things live

| Path                                  | Contents                                                     |
| ------------------------------------- | ------------------------------------------------------------ |
| `DESIGN.md`                           | Complete v0.1 design — read this first                       |
| `research/langgraph_patterns_2026.md` | Why LangGraph was rejected as the substrate                  |
| `research/ai_dev_pain_points_2026.md` | Loudest 2026 complaints that justify the project's existence |

## What this is, in one paragraph

Two data sources feed one unified `runs/<id>/` schema:

1. **Claude Code hooks** — `agentlog init` registers `SessionStart` / `UserPromptSubmit` / `PostToolUse` / `Stop` / `SessionEnd` handlers. Captures every interactive `claude` session automatically.
2. **SDK sidecar** — `agentlog tail <dir>` ingests existing `cc_raw_output.jsonl` files from scripted Claude Code SDK or Anthropic SDK runs.

CLI: `agentlog init / uninstall / tail / ls / cost / view`. Pure Python 3.11+, MIT, filesystem-first, no SaaS.

## Hard rules (DO NOT VIOLATE)

These are non-negotiable v0.1 constraints. Future Claude sessions must respect them.

1. **Hook handlers run in Claude Code's hot path.** Budget: <10ms steady-state, <50ms cold-start. NO network calls in hooks. Defer all analysis to read-time (CLI commands), never write-time (hooks).
2. **Fail-open always.** Hook handlers exit 0 even on internal errors. Errors log to `~/.agentlog/_self.log`. A buggy handler must NEVER break someone's Claude Code session.
3. **Never auto-install hooks.** `agentlog init` is an explicit command. NEVER mutate `~/.claude/settings.json` during `pip install`.
4. **No cost-budget kill-switch in v0.1.** Cut by operator decision — too complex, too high risk for first release. Deferred to v0.2+.
5. **`PreToolUse` hook deferred past v0.1.** Blocking hooks are the highest-risk surface; earn user trust with logging first.
6. **Local-first.** No SaaS, no required network calls. OTEL/network export opt-in only, planned for v1.0+.
7. **Schema versioned.** All event JSONL records include `schema_version: 1`. On Anthropic hook payload mismatch, log warning and write raw payload — don't crash.
8. **`init` must preserve existing hooks.** Merge, don't overwrite. Re-running must be idempotent.

## v0.1 ship scope (LOCKED)

1. `agentlog init` / `uninstall` — hook installation, idempotent, dry-run
2. Hook handlers: `SessionStart` / `UserPromptSubmit` / `PostToolUse` / `Stop` / `SessionEnd`
3. `agentlog tail <dir>` — SDK sidecar mode
4. `agentlog ls` — unified view across both sources
5. `agentlog cost <id>` — token + $ per phase
6. `agentlog view <id>` — basic TUI via `rich`
7. Docs + README + one demo gif

Target: 3-4 weeks for one person.

### Explicitly NOT in v0.1 (deferred, see DESIGN.md for v0.2+)

- Native Python API (`with agentlog.run(...)`)
- Subprocess wrapper (`agentlog.subprocess(...)`)
- Cost-budget kill-switch
- `agentlog diff <a> <b>`
- `agentlog replay <id>` (beyond basic event streaming)
- OTEL exporter
- LangGraph / claude-code-sdk plugins
- Web dashboard (never — local-first principle)

## Audience frame

A+C: career-signal portfolio artifact + thought-leadership blog companion. NOT pursuing OSS adoption metrics. README hero artifact + one demo gif + one launch blog post. Cross-link via existing `ai_coding_workflows` (⭐13) repo as the funnel.

## Stack

- Pure Python 3.11+
- Stdlib only in core: subprocess, json, sqlite3, pathlib
- Optional deps: `rich` (TUI), `textual` (v0.2 richer TUI), `opentelemetry-sdk` (v1.0+)
- Storage: filesystem (JSONL + small SQLite index for `ls` queries)
- Distribution: `pip install agentlog` AND `uv tool install agentlog`
- License: MIT

## Code provenance (extracted from bbworkflow, sanitized)

The observability primitives were lifted from a private bug-bounty automation framework. Sanitization removed all bb-specific tradecraft, target lists, scope YAML, deepeners, and historical data. Future Claude sessions in this repo should NOT reference bbworkflow internals or assume access to them.

Sources lifted:

- bbworkflow `adw_modules/utils.py::setup_logger` → `agentlog/log.py`
- bbworkflow `adw_modules/agent.py` (JSONL capture pattern) → `agentlog/capture.py`
- bbworkflow `travis/travis_state.py` → `agentlog/state.py`
- bbworkflow `travis/travis_sdlc.py` → `examples/sdlc_orchestrator.py` (sanitized)

## Open questions to resolve before coding starts

(Full context in `DESIGN.md`, "Open questions" section)

1. Hero artifact for README (screenshot / asciinema / cost-comparison)
2. GitHub repo description (one line)
3. Model pricing table source (hardcoded vs fetched vs user JSON)
4. Run-ID strategy for SDK mode (auto-derive vs explicit)
5. 60-second first-demo script

## Why this exists (the elevator pitch)

> Running AI coding agents is observability-blind. You wake up to $6,000 Claude bills. Agents claim they did things they didn't do. You can't compare today's run to yesterday's. LangSmith / Helicone / Phoenix trace single LLM API calls — they don't understand worktrees, branches, or the lifecycle of a coding agent. agentlog drops in once, captures every Claude Code session, and gives you the cost view, replay, and comparability you've been missing.
