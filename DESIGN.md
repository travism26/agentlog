# agentlog — Design Checkpoint (v0.1)

**Status**: design locked, pre-implementation
**Date**: 2026-05-26
**Author**: travis (via grill-me session)
**Future home**: `github.com/travism26/agentlog`
**Related research**:

- `research/langgraph_patterns_2026.md` — established LangGraph is the wrong frame; pure-Python sidecar wins
- `research/ai_dev_pain_points_2026.md` — established cost-runaway + "agent lying" as the two loudest 2026 pains

---

## One-liner

> **Observability for AI coding agents — captures both SDK-driven runs and interactive Claude Code sessions into one unified, replayable log.**

## Problem

Running AI coding agents is observability-blind:

- **Cost surprise.** Devs wake up to $6,000 Claude bills after leaving agents running overnight. No spend visibility per session, per phase, per workflow.
- **Agent lying.** Agents claim they did things they didn't do ("logs clearly show it never even tried to make the call"). No structured way to verify after the fact.
- **No comparability.** Can't compare today's run to yesterday's. Can't compare two workflows on the same ticket. The "is this getting better?" question has no answer.

Existing tools (LangSmith, Helicone, Phoenix, AgentOps) trace single LLM API calls. They do not understand worktrees, branches, parent/child sessions, or the _lifecycle_ of a coding agent. They were built for chatbot observability, not coding-agent observability.

## Dogfood origin

This design document was hand-written at the end of a 90-minute Claude Code grill-me session because there was no agentlog yet. To preserve what we decided, the operator had to manually persist a 480-line design doc, a project memory file, and update an index — a workflow that should not exist. The fact that THIS file had to be written by hand is the clearest statement of the problem agentlog solves: every Claude Code session evaporates unless you explicitly persist its decisions. Once installed, agentlog captures the conversation automatically and the design doc becomes a derived artifact, not a manual chore.

## What this is NOT

To stay sharp and avoid scope creep:

- NOT another agent framework (Cursor / Cline / Aider / Devin / OpenHands already exist)
- NOT a LangGraph alternative
- NOT a hosted SaaS — local-first, your data stays on your machine by default
- NOT a competitor to LangSmith — composes alongside (OTEL export in later versions)
- NOT a benchmark / leaderboard (separate project if pursued later)

## Audience (concentric circles)

| Tier                | Who                                                                                       | Why they care                                                                    |
| ------------------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **Core (v0.1)**     | Any Claude Code user; AI engineers who script Claude Code SDK / Anthropic SDK             | Hooks-mode captures every chat session for free; SDK-mode captures scripted runs |
| **Adjacent (v0.5)** | Internal-tools devs who built "ticket → PR" pipelines at their company                    | Want cost visibility and replay before deploying internally                      |
| **Outer (v1.0)**    | Platform engineers running AI agents at org scale; researchers benchmarking coding agents | Want OTEL export, cost attribution per dev/feature                               |
| **Explicitly NOT**  | Cursor/Devin/Codex-hosted users; ChatGPT browser users                                    | Their vendor owns observability for them                                         |

## Differentiator (the one-sentence claim)

> **Drop in once with `agentlog init` — captures every Claude Code session AND every scripted SDK run into the same unified log. Local-first, no SaaS, MIT.**

Nobody else has the hook-based unified model. LangSmith requires you to wrap every LLM call in their SDK. Helicone is a proxy. AgentOps is hosted. agentlog is a local CLI that ingests existing artifacts plus capturable hook events. **The integration friction is the smallest in the category.**

## Use cases (concrete)

The v0.1 capture substrate enables these queries — some shipped in v0.1, others built on top in later versions:

1. **"I left agents running overnight — show me what they cost"** (v0.1) → `agentlog ls --since 8h --sort cost`
2. **"This agent claimed it added the feature but it didn't"** (v0.1) → `agentlog view <id>` to step through CC tool calls and see actual diffs
3. **"How much did this chat session just cost me?"** (v0.1) → `agentlog cost <id>` against any captured session
4. **"Conversation history retention — what did we decide last week?"** (v0.1 captures the transcript; v0.2+ adds search/summarize) → today: `agentlog view <id>` to scroll; later: `agentlog search <query>`, `agentlog summarize <id>`
5. **"Reproduce yesterday's broken run for debugging"** (v0.2+) → `agentlog replay <id> --interactive`
6. **"Workflow A vs workflow B on the same ticket — which won?"** (v0.2+) → `agentlog diff run_a run_b`
7. **"Stop the run if it crosses $2"** (v0.2+) → `--max-spend $2` flag (deferred per operator; high-risk PreToolUse blocking hook)
8. **"Send all runs to our company's Honeycomb/Datadog"** (v1.0+) → `agentlog export --otel`

The "conversation history" use case (#4) is the dogfood case — every grill-me / brainstorm / debugging session today evaporates the moment Claude Code exits. agentlog v0.1 closes the capture gap; v0.2+ closes the query gap.

---

## Architecture

### Coupling model — LIGHT FIRST, hard second

| Version   | Coupling                                                                   | Integration                                                             |
| --------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| **v0.1**  | Light (file tail + Claude Code hooks)                                      | Zero code change for SDK users; one-time `agentlog init` for hook users |
| **v0.2+** | + Subprocess wrapper                                                       | One-line wrap for cost tagging                                          |
| **v1.0+** | + Native Python API + plugins (LangGraph callback, claude-code-sdk plugin) | Greenfield projects only                                                |

**Reasoning**: the research warned against competing with mature tools by being a worse version. Start as a sidecar; earn the right to be opinionated by being useful first. Tools that ingest existing artifacts win adoption fights.

### Data flow

```
                                      ┌──────────────────────┐
                                      │ runs/<id>/           │
                                      │   state.json         │
  ┌────────────────────┐              │   events.jsonl       │
  │Interactive `claude`├─── hooks ───▶│   cost.json          │
  │   session          │              │   _logs/             │
  └────────────────────┘              │                      │
                                      │ (unified schema)     │
  ┌────────────────────┐              │                      │
  │ SDK / subprocess   ├─── tail  ───▶│                      │
  │ (cc_raw_output.jsonl)             │                      │
  └────────────────────┘              └──────────┬───────────┘
                                                 │
                                                 ▼
                                      ┌──────────────────────┐
                                      │ agentlog CLI         │
                                      │   ls / view / cost   │
                                      └──────────────────────┘
```

### Unified `runs/<id>/` schema

Both data sources produce the same directory structure:

```
runs/
├── sdk-fix-bug-1234/              # SDK mode (subprocess tail)
│   ├── state.json                 # phases, outcomes, tags, totals
│   ├── events.jsonl               # append-only timeline
│   ├── cost.json                  # token + $ per phase
│   └── _logs/                     # subprocess stdout/stderr
└── cc-session-abc123/             # Interactive mode (hooks)
    ├── state.json
    ├── events.jsonl
    ├── cost.json
    └── _logs/
```

Run IDs:

- SDK mode: caller-supplied (`run_id="fix-bug-1234"`) or derived from cwd + timestamp
- Hooks mode: Claude Code's `session_id` (provided in every hook payload)
- Subagent sessions: include `parent_session_id` in `state.json` for fleet view

---

## Hook integration (the critical addition)

Claude Code's hook system fires shell commands on lifecycle events. agentlog registers handlers for:

| Hook               | When                        | Handler action                                      |
| ------------------ | --------------------------- | --------------------------------------------------- |
| `SessionStart`     | New `claude` session begins | Create `runs/<session_id>/`, write session metadata |
| `UserPromptSubmit` | User submits a prompt       | Append `prompt` event to `events.jsonl`             |
| `PostToolUse`      | After any tool call         | Append tool call + result to `events.jsonl`         |
| `Stop`             | Claude finishes a response  | Flush usage data, update cost totals                |
| `SessionEnd`       | Session terminates          | Finalize state, write summary                       |

**`PreToolUse` is intentionally NOT used in v0.1.** Blocking hooks are the highest-risk surface; a buggy handler would break someone's Claude Code session. Deferred until cost-guard feature lands in v0.2+.

### Performance contract (NON-NEGOTIABLE)

Hooks run in the hot path of every tool call. Latency budget:

| Operation                 | Budget        |
| ------------------------- | ------------- |
| Hook handler cold start   | <50ms         |
| Hook handler steady state | <10ms         |
| JSONL write               | <2ms          |
| Any network call in hook  | **FORBIDDEN** |

Implementation rules:

- Handlers exit 0 ALWAYS (fail-open — never break Claude Code)
- All analysis deferred to read-time (CLI commands), never write-time (hooks)
- Use a tiny shim binary or `uv tool` rather than spinning up a fresh Python interpreter
- Self-errors logged to `~/.agentlog/_self.log` for later debugging
- One bad install must not ruin someone's workflow — adoption depends on this

### Installation surface

```bash
agentlog init                  # Idempotent — adds hooks to ~/.claude/settings.json
agentlog init --project        # Writes to ./.claude/settings.json (project-scoped)
agentlog init --dry-run        # Print changes, write nothing
agentlog uninstall             # Removes agentlog hooks cleanly (trust signal)
```

`init` must:

- Be idempotent (re-running doesn't duplicate)
- Preserve existing hooks (merge, don't overwrite)
- Require explicit invocation (NEVER auto-install during `pip install`)
- Print exactly what will change before writing

---

## v0.1 ship scope (LOCKED)

| #   | Component                                                                              | Effort   | Notes                                                     |
| --- | -------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------- |
| 1   | `agentlog init` / `uninstall`                                                          | 1 day    | Write/remove hook entries; idempotent; dry-run            |
| 2   | Hook handlers: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd` | 2-3 days | <10ms each; fail-open; tiny shim                          |
| 3   | `agentlog tail <dir>` (SDK sidecar mode)                                               | 1-2 days | Lift JSONL parsing from bbworkflow `adw_modules/agent.py` |
| 4   | `agentlog ls` (unified view)                                                           | 1 day    | SQLite index across both sources                          |
| 5   | `agentlog cost <id>`                                                                   | 1-2 days | Parse `usage` blocks; multiply by model pricing table     |
| 6   | `agentlog view <id>` (basic TUI via `rich`)                                            | 3-4 days | Hero artifact — the README screenshot                     |
| 7   | Docs, README, one demo gif                                                             | 2-3 days | A+C audience demands polish                               |

**Total: 3-4 weeks for one person.**

### Explicit non-goals for v0.1

To prevent scope creep — these are GOOD ideas, deferred:

- ❌ **Cost-budget kill-switch / `--max-spend $X`** — explicitly cut per operator direction (too complex, too high risk for first release). Documented as v0.2+ candidate.
- ❌ Native Python API (`with agentlog.run(...)`) — v0.2+
- ❌ Subprocess wrapper (`agentlog.subprocess(...)`) — v0.2+
- ❌ `agentlog diff <a> <b>` — v0.2+
- ❌ `agentlog replay <id>` — v0.2+ (basic event-stream replay only)
- ❌ `agentlog search <query>` — v0.2+ (full-text search across captured session transcripts; dogfood case for conversation history)
- ❌ `agentlog summarize <id>` — v0.2+ (LLM-driven session summary: "what did we decide?")
- ❌ Cross-session semantic memory — v1.0+ (find old conversations that discussed X)
- ❌ OTEL exporter — v1.0+
- ❌ LangGraph / claude-code-sdk plugins — v1.0+
- ❌ Web dashboard — not on the roadmap, possibly never (local-first principle)

---

## Risks and mitigations

| Risk                                           | Mitigation                                                                                     |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Hook latency degrades Claude Code performance  | <10ms budget; tiny shim; defer all analysis to read-time; benchmark before release             |
| Hook crash breaks user's Claude Code session   | Fail-open: exit 0 always; log self-errors to separate file                                     |
| Cost data missing in some events               | Document which events carry `usage`; attribute spend to surrounding phase via timestamps       |
| Multiple parallel sessions collide             | Key everything by Claude Code's `session_id`; test with 3+ concurrent sessions                 |
| Subagent sessions lose parent linkage          | Capture `parent_session_id` from hook payload; expose in `state.json`                          |
| Anthropic changes hook payload schema          | Version event schema (`schema_version: 1`); log warnings on mismatch; write raw payload anyway |
| Privacy: prompts contain sensitive data        | Local-only by default; OTEL/network export is opt-in; lead README with this                    |
| User installs and tools break, blames agentlog | Comprehensive `uninstall` command; clean error messages from `init`; never auto-install        |

---

## Implementation notes

### Stack

- **Language**: Pure Python 3.11+
- **Distribution**: `pip install agentlog` AND `uv tool install agentlog`
- **Hot-path runtime**: bare-bones Python or a tiny shim — must start fast
- **Core deps**: stdlib only (subprocess, json, sqlite3, pathlib)
- **Optional deps**: `rich` (TUI), `textual` (richer TUI for v0.2)
- **Storage**: filesystem (JSONL + small SQLite index for `ls` queries)
- **License**: MIT

### Code lifted from bbworkflow (sanitized)

| bbworkflow source                              | agentlog destination                                          |
| ---------------------------------------------- | ------------------------------------------------------------- |
| `adw_modules/utils.py::setup_logger`           | `agentlog/log.py`                                             |
| `adw_modules/agent.py` (JSONL capture pattern) | `agentlog/capture.py`                                         |
| `travis/travis_state.py`                       | `agentlog/state.py`                                           |
| `travis/travis_sdlc.py`                        | `examples/sdlc_orchestrator.py` (sanitized, no bb tradecraft) |

### What to STRIP when extracting

- All `.claude/skills/vuln-*` (bb-specific)
- Deepeners, scope YAML, target lists
- Historical `agents/*/` data
- All `research/` content referencing real targets
- Any program-rules / impact-floor logic
- Anything mentioning "bug bounty," "HackerOne," "Yahoo," etc.

---

## Brand and positioning

### Name: `agentlog`

Chosen for:

- Boring-infrastructural per research recommendation
- Reads like `structlog` / `httplog` — analogue is immediately obvious
- No vendor lock-in (ages past Claude / Anthropic / Cursor)
- Easy to type
- No collision with existing PyPI packages (verify before claiming)

Anti-picks rejected: `claude-trace` (vendor-locked), `swarmscope` (sounds like SaaS), `adw` (insider acronym), `coderun` (too generic), `runtap` (cute but less defensible).

### Repo tagline (under repo description on GitHub)

> _"Local-first observability for AI coding agents. Captures every Claude Code session — interactive and scripted — into one replayable log."_

### Funnel

Three repos, one narrative:

- `ai_coding_workflows` (existing, ⭐13) — AI dev tips & ideas; funnel readers to agentlog
- `claude_code_agent_templates` (existing) — link in agentlog README as related work
- `agentlog` (new) — the artifact

Cross-link aggressively. README at the top of each mentions the others.

---

## Decisions log (so future-me remembers WHY)

| Decision                                                         | Why                                                            |
| ---------------------------------------------------------------- | -------------------------------------------------------------- |
| Audience: career-signal + thought-leadership (A+C)               | Operator stated top-1% goal; not pursuing OSS adoption metrics |
| Pivoted away from spec-driven framework benchmark                | Operator cooled on the idea; too taste-litigious               |
| Use Claude Code hooks (not binary wrapping or chat-UI injection) | Hooks are the supported API; respect the contract              |
| Cut cost-budget kill-switch from v0.1                            | Operator: "complex and high risk"; defer to v0.2+              |
| `agentlog` over `runtap` / `agentd` / `swarmscope`               | Boring-infrastructural reads strongest in 2026                 |
| Filesystem-first storage, no database                            | Local-first principle; SQLite index for query speed only       |
| Light coupling first (sidecar + hooks), native API later         | Adoption wins fights against mature tools                      |
| `PreToolUse` deferred past v0.1                                  | Blocking hooks are high-risk; earn trust with logging first    |
| Don't auto-install hooks during `pip install`                    | One bad surprise mutation of settings.json burns trust forever |

---

## Open questions (for next session)

These were not decided in this grill-me session and should be addressed before coding starts:

1. **Hero artifact** — what's the _one_ image at the top of the README? A static screenshot of `agentlog view`, an asciinema cast of `agentlog init` + first chat session, or a side-by-side cost comparison? Pick one before writing code.
2. **GitHub repo description (single line)** — locks the first impression. Candidate: _"Local-first observability for AI coding agents — captures Claude Code sessions AND SDK runs."_
3. **Model pricing table source** — hardcoded? fetched from Anthropic? user-provided JSON? v0.1 can ship with a hardcoded table; v0.2 needs a strategy.
4. **Run-ID strategy for SDK mode** — auto-derive vs require explicit? Convention vs config?
5. **First demo task** — what does the user run on their machine to see value in 60 seconds? Probably: install → start a `claude` session → run a few prompts → `agentlog view <session>`. Script this end-to-end before declaring v0.1 ready.

---

## What to do NEXT (when ready to start)

1. Create the repo: `github.com/travism26/agentlog`
2. Lift this file into the new repo as `DESIGN.md`
3. Decide the 5 open questions above (15-minute session)
4. Scaffold the package layout
5. Implement v0.1 components in the order listed in the ship scope table
6. Manual smoke test against your own Claude Code sessions
7. Cross-link from `ai_coding_workflows` README
8. Write the launch blog post: _"How I built observability for my AI coding agents — and the $6,000 bill I never want to see again"_
