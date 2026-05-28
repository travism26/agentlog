# agentlog

**Local-first observability for AI coding agents — captures Claude Code sessions AND SDK runs into one unified, replayable log.**

```
┏━━━━━━━━━━━━━━━━━━━━━━ sdk-f50fb891-7340-491d-99c5-695855e93a79 ━━━━━━━━━━━━━━━━━━━━━━┓
┃ Source:    sdk                                                                       ┃
┃ Model:     claude-opus-4-7                                                           ┃
┃ Cwd:       /Users/rickjms/code/agentlog                                              ┃
┃ Started:   2026-05-27T08:48:48Z                                                      ┃
┃ Duration:  2m54s                                                                     ┃
┃ Events:    21                                                                        ┃
┃ Cost:      $1.6082                                                                   ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
TIMELINE
┌ 08:48:48Z  session_start   cwd=/Users/rickjms/code/agentlog
│ 08:48:57Z  unknown         original_type=rate_limit_event (raw size: 636)
│ 08:49:05Z  unknown         original_type=system (raw size: 557)
│ 08:49:32Z  tool_use        Read      /tmp/agentlog_init_prompt.md
│ 08:49:40Z  tool_use        Read      ./DESIGN.md
│ 08:49:49Z  tool_use        Bash      ls -la ./
│ 08:49:58Z  tool_use        Glob      **/*.py
└ 08:50:24Z  tool_use        Read      ./tests/test_cli_smoke.py
… (14 more events; use --limit 0 to see all)
COST
               Tokens  Rate (per 1M)     Cost
------------  -------  -------------  -------
Input           2,999      $15.00/1M  $0.0450
Output          8,047      $75.00/1M  $0.6035
Cache read    310,671       $1.50/1M  $0.4660
Cache create   26,330      $18.75/1M  $0.4937
------------  -------  -------------  -------
Total         348,047                 $1.6082
```

> Running AI coding agents is observability-blind. You wake up to $6,000 Claude bills. Agents claim they did things they didn't. You can't compare today's run to yesterday's. LangSmith / Helicone / Phoenix trace single LLM API calls — they don't understand worktrees, branches, or the lifecycle of a coding agent. **agentlog drops in once, captures every Claude Code session, and gives you the cost view, replay, and comparability you've been missing.**

## Status

Pre-release. v0.1 design locked 2026-05-26, ship-scope items 1–6 implemented, docs in progress. See [DESIGN.md](DESIGN.md) for the locked scope.

The development of agentlog itself was captured by agentlog — running `agentlog tail agents/` against this repo will rehydrate **42 SDK sessions worth $51.10** of Claude Code calls that built it.

## Install

```bash
pip install 'agentlog[tui]'
# or
uv tool install 'agentlog[tui]'
```

The `[tui]` extra pulls in `rich` for `agentlog view`. The base install (`pip install agentlog`) is stdlib-only — all subcommands except `view` work without `rich`.

Python 3.11+, MIT licensed, no SaaS, no required network calls.

## 60-second tour: hooks mode (interactive Claude Code)

```bash
# 1. Register agentlog as a Claude Code hook handler. Idempotent, dry-run-able.
$ agentlog init
installed agentlog hooks to ~/.claude/settings.json

# 2. Use Claude Code normally. Every session is captured automatically.
$ claude
> explain this codebase
…
> implement the feature in specs/foo.md
…

# 3. See what your agents have been doing.
$ agentlog ls
RUN ID                                    SOURCE  STARTED               DUR      EVENTS  TOKENS    MODEL
abc123-...                                hooks   2026-05-27T15:32:11Z  4m12s    18      48,290    claude-opus-4-7
abc124-...                                hooks   2026-05-27T15:38:47Z  1m05s    4       3,118     claude-opus-4-7

# 4. See what it cost.
$ agentlog cost abc123-...
Total tokens: 48,290  →  $0.4127

# 5. See what it actually did. (The hero shot above.)
$ agentlog view abc123-...
```

`agentlog init` is **explicit and reversible**. It never auto-installs during `pip install`, it merges into your existing `settings.json` (never overwrites foreign hooks), and `agentlog uninstall` cleanly removes only what agentlog added.

## 60-second tour: SDK sidecar mode (scripted Claude Code SDK / Anthropic SDK)

If you script Claude Code via subprocess and end up with `cc_raw_output.jsonl` files on disk, `agentlog tail` ingests them into the same unified schema as live hook captures.

```bash
# Ingest a single file or walk a directory of them.
$ agentlog tail ./agent_runs/
./agent_runs/research/cc_raw_output.jsonl → ~/.agentlog/runs/sdk-f50fb891-...  (21 events)
./agent_runs/build/cc_raw_output.jsonl    → ~/.agentlog/runs/sdk-c97e5591-...  (56 events)
./agent_runs/test/cc_raw_output.jsonl     → ~/.agentlog/runs/sdk-4eefbae7-...  (8 events)

# Now `ls` / `cost` / `view` work uniformly across hooks-mode AND SDK-mode runs.
$ agentlog cost --all
…
TOTAL  (42 runs)                                          29,919,170  $51.0998
```

The schema is identical for both sources. A hooks-mode session and a SDK-mode session land in the same `~/.agentlog/runs/<id>/` directory shape — `state.json` + `events.jsonl` + `cost.json`. Any read-time tool that works on one works on the other.

## What you get

- **One command to capture every Claude Code session.** `agentlog init` registers five hook handlers (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`). Hot-path latency budget: <10ms steady-state, <50ms cold-start. Fail-open always — a buggy handler will never break your Claude Code session.
- **One CLI to ingest scripted SDK runs.** `agentlog tail <dir>` lifts every `cc_raw_output.jsonl` into the same `runs/<id>/` schema. The data sources fold into one timeline.
- **Cost visibility, per run.** `agentlog cost <id>` multiplies recorded token totals by a built-in Anthropic pricing table (overridable via `--pricing` or `$AGENTLOG_PRICING`). `agentlog cost --all` rolls up across every captured run, sorted by spend.
- **Replay, per run.** `agentlog view <id>` renders a three-panel TUI: run metadata, event-by-event timeline (color-coded by kind, per-tool params extracted), cost footer. Pipe to `less -R` for long sessions.
- **Local-first.** Everything lives under `~/.agentlog/`. No SaaS. No required network calls. OTEL export is on the roadmap for v1.0+, opt-in.
- **Stdlib-only core.** `pyproject.toml dependencies = []`. `rich` is an optional `[tui]` extra used only by `view`. Distribution: `pip install agentlog` or `uv tool install agentlog`.

## What this is NOT

- Not another agent framework (Cursor / Cline / Aider / Devin / OpenHands already exist).
- Not a LangGraph alternative. agentlog composes alongside any orchestrator that ultimately calls `claude` or the Anthropic SDK.
- Not a hosted SaaS. Your data stays on your machine by default.
- Not a LangSmith competitor. They trace single LLM API calls; agentlog understands sessions, worktrees, hooks, and the lifecycle of a coding agent. OTEL export in v1.0+ for users who want both.
- Not a benchmark / leaderboard. Separate project if pursued later.

## Architecture in 30 seconds

```
                                          ┌──────────────────────┐
                                          │  ~/.agentlog/runs/   │
                                          │    <id>/             │
  ┌────────────────────┐                  │      state.json      │
  │ Interactive claude ├─── hooks ──────▶ │      events.jsonl    │
  │    session         │                  │      cost.json       │
  └────────────────────┘                  │                      │
                                          │  (unified schema)    │
  ┌────────────────────┐                  │                      │
  │ SDK / subprocess   ├─── tail ───────▶ │                      │
  │ (cc_raw_output)    │                  │                      │
  └────────────────────┘                  └──────────┬───────────┘
                                                     │
                                                     ▼
                                          ┌──────────────────────┐
                                          │  agentlog CLI        │
                                          │    ls  cost  view    │
                                          └──────────────────────┘
```

Two ingest paths, one schema, three read-time tools. Full design in [DESIGN.md](DESIGN.md); the in-repo agentic-developer-workflow that built it is documented in [docs/adw-lessons.md](docs/adw-lessons.md).

## CLI reference

| Command | Purpose |
|---|---|
| `agentlog init` / `uninstall` | Register / remove hook handlers in `.claude/settings.json`. Idempotent, dry-run-able, project-scoped via `--project`. |
| `agentlog tail <path>` | Ingest `cc_raw_output.jsonl` from SDK runs. Idempotent; re-run with `--force` to overwrite. |
| `agentlog ls` | Unified list of all captured runs. SQLite index for query speed. Filter by `--source` / `--since`, sort by `--sort tokens|cost|started|...`. `--json` for scripting. |
| `agentlog cost <id>` / `--all` | Token-to-dollar rollup. Built-in pricing table; override with `--pricing <file>` or `$AGENTLOG_PRICING`. |
| `agentlog view <id>` | Three-panel TUI for a single run (requires `[tui]` extra). `--json` bypasses rich for scripting. |

See [docs/cli-reference.md](docs/cli-reference.md) for the full flag matrix.

## Privacy

agentlog captures everything Claude Code's hook payloads contain — prompts, tool calls (including file contents read), and assistant responses. **This data stays local by default.** Nothing is sent anywhere unless you explicitly opt into (a future) OTEL export. If your prompts contain secrets, treat `~/.agentlog/runs/` like any other log directory: rotate, scrub, or `.gitignore` it.

## Related projects

- [ai_coding_workflows](https://github.com/travism26/ai_coding_workflows) — AI development tips, agent ideas, prompt patterns
- [claude_code_agent_templates](https://github.com/travism26/claude_code_agent_templates) — reusable Claude Code agent definitions

## License

MIT. See [LICENSE](LICENSE).
