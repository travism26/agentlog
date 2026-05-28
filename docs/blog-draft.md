# I Built an Observability Tool for AI Coding Agents. It Cost $30 to Ship — Plus the Bug I Shipped Without Noticing.

> **Series context:** This is a companion to the [6 months of ADW retrospective](https://dev.to/rickjms/...) and the broader [ai_coding_workflows](https://github.com/travism26/ai_coding_workflows) series. Previous posts covered the Validate phase, state management, rate-limit resilience, token cost optimization, and the Review severity taxonomy. This one is about what happens when you point that whole pipeline at *building observability for the pipeline itself*, what the receipts actually look like when the agent watches the agent watch the agent, and the bug I shipped to GitHub before I noticed it was sitting in the founding use case.

---

A friend told me last month he woke up to a **$6,000 Claude bill**. He'd left a coding agent running overnight against a feature branch. It hadn't finished the feature. He couldn't tell you what it had actually done — just that there were a few commits, the diff was nonsense, and the bill was real.

I had a smaller version of the same experience a week earlier. Not $6,000, but enough to notice. And the same blank-look moment when I tried to figure out which agent invocation had eaten the budget. The shell scrollback was gone. The "logs" were a tarball of `cc_raw_output.jsonl` files that didn't decode into anything I could grep.

Existing observability tools didn't help. LangSmith, Helicone, Phoenix, AgentOps — they all trace single LLM API calls. They're built for chatbot observability. They don't understand worktrees. They don't understand the difference between a 4-minute interactive `claude` session and a 45-minute scripted SDLC pipeline. They don't know what `cc_raw_output.jsonl` is.

So I built [agentlog](https://github.com/travism26/agentlog).

> **TL;DR for the skimmers:** Local-first CLI. `agentlog init` registers Claude Code hooks once. Every chat session and scripted SDK run lands in the same `~/.agentlog/runs/<id>/` schema. Three read-time tools (`ls`, `cost`, `view`) work over both data sources. Built in a week using my own ADW pipeline — total cost of building it, captured by itself, was **$30.73 across 48 runs**. Plus a punchline about a bug I shipped, found on launch day, and fixed in the same evening.

---

## What it actually does

Three commands. Two ingest paths. One schema.

```plaintext
┌─────────────────────────────────────────────────────┐
│                                                     │
│  Interactive `claude`  ──── hooks ───┐              │
│                                      ▼              │
│  SDK / subprocess      ──── tail ──▶ runs/<id>/     │
│   (cc_raw_output)                    state.json     │
│                                      events.jsonl   │
│                                      cost.json      │
│                                          │          │
│                                          ▼          │
│                            agentlog ls / cost / view│
│                                                     │
└─────────────────────────────────────────────────────┘
```

`agentlog view <id>` renders a three-panel TUI: run metadata, event-by-event timeline (color-coded by kind, per-tool params extracted), cost footer. Here's a real one:

```plaintext
┏━━━━━━━━━━━━━━━━━━━━━━ sdk-f50fb891-... ━━━━━━━━━━━━━━━━━━━━━━┓
┃ Source:    sdk                                                ┃
┃ Model:     claude-opus-4-7                                    ┃
┃ Cwd:       /Users/rickjms/code/agentlog                       ┃
┃ Duration:  2m54s                                              ┃
┃ Events:    21                                                 ┃
┃ Cost:      $0.54                                              ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
TIMELINE
┌ 08:48:48Z  session_start   cwd=/Users/rickjms/code/agentlog
│ 08:49:32Z  tool_use        Read   /tmp/agentlog_init_prompt.md
│ 08:49:40Z  tool_use        Read   ./DESIGN.md
│ 08:49:58Z  tool_use        Glob   **/*.py
│ ...
└ 08:50:50Z  stop            2m54s elapsed | 348,047 tokens
```

That's the "what did the agent actually do" view. When an agent claims to have implemented something, this is where you verify. The `tool_use` rows extract per-tool params — file paths for Read/Edit/Write, search patterns for Grep, commands for Bash. If the agent says it edited `cli.py` but the timeline shows it only Read'd it, you have the receipts.

`agentlog cost --all` is the "what did I just spend" view:

```plaintext
RUN ID                  MODEL              TOKENS      COST
sdk-be303e7f-...        claude-opus-4-7    1,285,817   $1.50
sdk-c33af919-...        claude-opus-4-7    1,097,998   $1.25
sdk-0f66d610-...        claude-opus-4-7    1,220,892   $1.22
...
TOTAL  (48 runs)                           33,421,621  $30.73
```

That $30.73 is real. It's what agentlog cost to *build itself*. Mostly.

The word "mostly" is doing some work in that sentence. Hold on to it — we'll come back to it after the dogfood loop.

---

## The dogfood loop

I built agentlog with my own ADW pipeline. Six phases per feature — research, plan, build, validate, test, review, document — each spawning a fresh `claude` subprocess against a slash command. Same orchestrator I covered in the [validate phase post](#) and the [state management post](#).

Each phase invocation produces a `cc_raw_output.jsonl` file. Those are exactly the files `agentlog tail` was designed to ingest. So as soon as ship-scope item #3 (the tail subcommand) landed, I pointed it at the `agents/` directory my own ADW had filled up while building items #1 and #2.

**Seven `sdk-*` run directories appeared.** The infrastructure that had been writing data nobody could read suddenly had a reader.

After item #4 (`agentlog ls`) landed, that became a 28-row table. After item #5 (`agentlog cost`), it became a dollar receipt. After item #6 (`agentlog view`), I could scroll through any of those 28 sessions and see exactly what the build phase had decided, line by line.

By the time docs were drafted: **48 sessions, $30.73 total, 33.4 million tokens.**

> **The whole pitch of the project is "make the cost surprise impossible by making the visibility default." It worked on me.**

---

## What the polish-pass loop taught me

Six SDLC passes is enough sample size to see patterns. Each pass produced 500–800 lines of well-typed Python, all tests passing on first attempt, a written plan, a written review, a generated doc page. The review phase flagged 0–4 issues per pass. None were blockers.

But as the project grew, I started seeing the *same* polish-pass patterns. Sort keys with wrong-direction sign flips. Module-level `assert` statements for invariants that should have been pytests. SQLite schema-version bootstrap orders that would silently break on future migration. Stale `# ship-scope item #N will replace this` comments still sitting above code that *was* ship-scope item #N.

By step 5 I'd seen four sort-key issues. So I stopped and wrote [`docs/adw-lessons.md`](https://github.com/travism26/agentlog/blob/main/docs/adw-lessons.md) — a 140-line file that catalogs eleven recurring patterns, each with a "Why" line naming the actual past incident and a "Test shape" block the next plan phase can copy-paste verbatim. Then I wired the file into the research, plan, and review slash commands so future ADW runs would pick it up.

The next SDLC pass (step 6 — the rich TUI) had three reviewer issues, and they were all "missing test for an enumerated edge case" — meta-level coverage gaps, not actual bugs. The reviewer cited the lessons file by number in its passing checks: *"Lesson #1 sort-order regression test exists, Lesson #2 no module-level asserts found, Lesson #4 no stale comments, Lesson #9 dispatch table present with invariant test."*

The actual bug *I* caught in step 6 wasn't in the reviewer's set at all — `view` rendering exposed a `tail` timestamp bug. The lessons file moved the reviewer up the abstraction stack so I could focus on the cross-module stuff the reviewer structurally can't see.

> **The polish-loop pattern isn't a one-time tax — it's a feedback channel. If you find yourself fixing the same class of bug twice across an AI-built codebase, that's the moment to write down the rule and wire it into your reviewer prompt. Twenty minutes of meta-work saves twenty minutes per future pass, forever.**

---

## The bug shipping the tool caught

So I shipped v0.1. Pushed to GitHub. Tests green, lints clean, docs done. I opened a fresh shell, ran `agentlog init` on my actual user-global `~/.claude/settings.json`, opened a fresh Claude Code session, sent a test prompt, exited.

```bash
$ agentlog ls --limit 1
RUN ID                  SOURCE  STARTED               DUR      EVENTS  TOKENS  MODEL
4bae6f10-...            hooks   2026-05-28T19:57:38Z  7m11s    6       0       claude-opus-4-8[1m]
```

Tokens: **0**. The "$6,000 surprise bill" feature. The headline use case. The blockquote at the top of this very post. Zero.

I had unit tests for `_on_stop`. They all passed. They passed because I'd seeded the test payloads with a synthetic `usage` block in the exact shape Anthropic's API documentation suggested:

```python
payload = {"session_id": "x", "usage": {"input_tokens": 100, ...}}
```

What Claude Code's actual `Stop` hook payload contains: **no `usage` block at all.** Just `session_id`, `transcript_path`, `hook_event_name`, and `stop_hook_active`. The authoritative source for token totals is the transcript file the payload points at — which my handler was completely ignoring.

The reviewer phase never caught this. It couldn't. The reviewer sees the diff and the spec. The spec said "extract usage from the Stop payload"; the implementation extracted usage from the Stop payload. Both technically correct. The bug was at the seam between "what I told the agent" and "what Anthropic actually emits" — a kind of bug that lives outside the spec's frame of reference.

The fix was a 90-line transcript-tail reader. Bounded I/O so it doesn't violate the 10ms hot-path budget on long sessions. Defensive fallback to zero on any read failure. A regression test against a 10MB synthetic transcript to lock the bound. Three real tests for the happy path, the precedence rule (explicit payload wins if Anthropic ever adds usage back), and the graceful-degrade case.

But the launch-day shell session caught two more things in the same evening:

1. **My pricing table was wrong.** The session model was `claude-opus-4-8[1m]`. My table had `claude-opus-4-7` with $15/$75 per million tokens. Wrong on both counts. Anthropic dropped Opus pricing **3×** when 4.7/4.8 launched ($5/$25), and 4.8 wasn't in the table at all. The `[1m]` suffix is Claude Code's marker for the 1M-context variant. Everywhere my "$" number appeared in this post and in my own README, it was overstated by roughly 3×.
2. **My pricing assumption was wrong.** Claude Code has two billing modes. API users pay per-token at the rates I'd built around. **Subscription users — Pro at $20/mo, Max 5x at $100/mo, Max 20x at $200/mo, Team Premium at $100-125/seat — pay a flat monthly fee.** For them, my dollar amounts are not just wrong, they're a category error. There's no per-token dollar; there's a fixed bill plus a quota. The token count still indicates *quota usage*, which is genuine signal. But "$X" suggests a marginal cost that doesn't exist.

The bug fix shipped as v0.1.1. The pricing table is now keyed by the base model id with suffix stripping. The footer on every `agentlog cost` output now explicitly flags the subscription caveat. The "real" dogfood number above ($30.73) is what API users would have paid; what *I* actually paid was zero marginal dollars on top of my existing subscription.

The reviewer-prompt + lessons file caught patterns. Live install caught contracts. Both matter. They're different defenses against different failure modes, and **I shouldn't have shipped without doing the live install check first.** That's the new entry at the top of the lessons file.

> **Tests verify behavior against a fixture. Reviews verify implementation against a spec. Only live install verifies your assumptions against the world. Skip the last step and the load-bearing feature ships broken.**

---

## Where my agentlog token budget actually went

For the curious. Same rough-orders-of-magnitude breakdown as the [token cost optimization post](#) but with real numbers from real runs (using the corrected pricing):

```plaintext
┌─────────────────────────────────────────────────────────┐
│  agentlog v0.1 build cost breakdown ($30.73 total)      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Plan      ████████████████        ~$9   Opus           │
│  Research  ██████████              ~$6   Opus           │
│  Build     ████████                ~$5   Sonnet         │
│  Review    ██████                  ~$4   Sonnet         │
│  Test      ████                    ~$3   Sonnet         │
│  Document  ███                     ~$2   Sonnet         │
│  Validate  ██                      ~$1   Sonnet         │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Plan and Research dominated, as expected. The cost-optimization playbook from the [token post](#) is doing its job here — Opus only on the phases where reasoning quality compounds (Plan, Research), Sonnet everywhere else. The single most expensive run was around **$3.50** — the planner for the `agentlog tail` feature, 1.28M tokens, doing discovery-heavy reads across the existing module structure.

If I'd run everything on Opus the way I did on my first ADW project six months ago, this would have been closer to $80 instead of $30. Cheaper-models-per-phase is the single biggest savings lever and it costs you nothing to keep using.

---

## Five things I'd build differently

If I started over tomorrow:

| Decision | Day One? | Why |
| --- | --- | --- |
| **Live-install verification before push** | **Yes** | Tests + review missed the founding-use-case bug because the contract between agent and Claude Code's actual payload was outside the test fixtures. A 30-second `pip install . && agentlog init && claude` would have caught it. |
| Hook handlers before install command | **Yes** | I built `init` first; it pointed at a no-op subparser for a week. The handlers are the load-bearing piece with the tightest contract (<10ms steady, fail-open). Build the constraints first. |
| Pydantic for the schema definition | **Yes** | Hard rule is "stdlib only for the hot path." That's correct for the *handlers*. The schema definition itself doesn't need it. Would have caught two typo'd-field-name bugs the build agent shipped. |
| ADW lessons file from day one | **Yes** | Wrote it at step 5. Should have been step 1, populated as patterns emerged instead of in arrears. |
| Textual for `view`, not just rich | **No, but soon** | Static rich render works as a hero shot. Useless for sessions with 200+ events. v0.2. |

The live-install verification is the new entry as of v0.1.1. The cost was 30 seconds of typing. The non-cost was shipping a tool whose headline value prop returned zero for the first user.

---

## Skeptic's Corner: "Won't Anthropic just ship native session export?"

Honestly? Probably, eventually. And that's fine.

agentlog doesn't compete with whatever Anthropic eventually ships as native session capture. It composes alongside it. The hook handlers register on a documented, supported API — if Anthropic adds a richer native capture format, the handlers adapt to consume it, and `agentlog tail` learns a new source flavor. The unified `runs/<id>/` schema doesn't care where the events came from.

The deeper bet is the same one I made in the [retrospective](#): **the patterns outlast the tools.** A future where Claude Code has native session export and agentlog ingests it is the same architecture I'm shipping today, just with one fewer parser to maintain.

The bigger skeptic question — the one I dodged for a week — is: **"Isn't the cost feature mostly meaningless for subscription users?"** It is, partly. If you pay $200/mo for Max 20x, the per-token dollar number agentlog computes is API-equivalent, not what you actually owe. The token *count* is still useful (it indicates quota usage, which the Claude Code UI doesn't expose well). The forthcoming `--billing` flag will let you tell agentlog "I'm on Pro" and have it show quota percentages instead of dollars. That's [issue #3](https://github.com/travism26/agentlog/issues/3), v0.2 scope.

> **If you're not in the local-first audience, your timing for using this tool is "never." If you are, your timing is "right now."**

---

## Try it

v0.1 isn't on PyPI yet — install straight from the repo:

```bash
pip install 'agentlog[tui] @ git+https://github.com/travism26/agentlog'
agentlog init
# go use claude normally for a few minutes
agentlog ls
agentlog cost --all
agentlog view <id-from-ls>
```

Repo: [github.com/travism26/agentlog](https://github.com/travism26/agentlog)
Design doc: [DESIGN.md](https://github.com/travism26/agentlog/blob/main/DESIGN.md)
ADW lessons file: [docs/adw-lessons.md](https://github.com/travism26/agentlog/blob/main/docs/adw-lessons.md) (the meta-thing this post was about)

---

## What's Next

I want to follow this up with a few more posts in the series:

- **The launch-day bug post-mortem in depth** — the cost-feature-returns-zero finding, the lessons-file gap that let it through, and the `tests/reviews/live-install` venn diagram. Same arc this post sketched but with the actual diff, the actual reviewer transcript, and a longer think on what "verification" means for AI-built code.
- **The pydantic-or-stdlib decision** — when "stdlib only" is actually the right call vs when it's a vestige of an earlier constraint. I made the wrong call on this one at first.
- **A walk-through of `agentlog view` against a real overnight run** — the kind that produces the $6,000 surprise. Showing the full timeline of what an agent actually did when nobody was watching.

If you've been running coding agents and hit different observability gaps than the ones I described here, I'd genuinely love to hear about them. The ones that surprised me are the ones that taught me the most.

Thanks for reading.

---

_Tags: `#ai` `#claudecode` `#agents` `#observability` `#opensource` `#python` `#tooling` `#dogfooding`_
