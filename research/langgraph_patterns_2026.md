# LangGraph Patterns Worth Adopting — 2026 Field Notes

Target reader: an AI-engineer power-user who already understands DAGs, state machines, and subprocess orchestration, and is deciding what LangGraph artifacts actually move the needle vs. what is demo-ware. Scope: load-bearing patterns, concrete repos, and honest anti-patterns. No LangGraph 101.

---

## 1. State + Checkpoint Patterns

LangGraph's killer feature is not the graph DSL — it's the checkpoint contract. Every node read/writes a serialized state snapshot, which gives you (a) durable resume after crash, (b) human-in-the-loop without a custom queue, (c) time-travel debugging, and (d) horizontal scaling across workers — all from one primitive.

### Patterns

**`InMemorySaver` → `SqliteSaver` → `PostgresSaver` progression.** Local dev uses memory; single-machine batch uses SQLite; production uses Postgres with the official `langgraph-checkpoint-postgres` package. The Postgres saver supports horizontal scaling: multiple API workers can advance different turns of the same `thread_id` against the same store without conflict. Production teams put PgBouncer in front once worker count exceeds ~10 and run a nightly TTL job, because _every node write creates a checkpoint row_ — million-turn threads will balloon the table.

- Earns its complexity when: you need (a) multi-worker scale, (b) interrupt/resume gaps measured in days, or (c) cross-thread memory via the `Store` API.
- Source: <https://pypi.org/project/langgraph-checkpoint-postgres/>, <https://sparkco.ai/blog/mastering-langgraph-checkpointing-best-practices-for-2025>

**Time-travel / fork via `update_state` + `get_state_history`.** `update_state` does _not_ roll back the thread — it creates a _new_ checkpoint that branches from a prior point, leaving original history intact. This is the underrated debugger: replay a failed run, edit the bad node's output, fork, re-execute downstream. It is the closest thing in the agent world to a `git rebase -i` for non-deterministic flows.

- Earns its complexity when: you've burned an afternoon trying to reproduce a flaky multi-step failure.
- Source: <https://langchain-ai.github.io/langgraph/concepts/time-travel/>, <https://docs.langchain.com/oss/python/langgraph/use-time-travel>

**Strict-msgpack deserialization.** Set `LANGGRAPH_STRICT_MSGPACK=true` or pass `allowed_msgpack_modules`. Default checkpoint deserialization will instantiate any pickled class in the table — if your DB is ever compromised, that's RCE on the orchestrator. This is a real CVE-class footgun nobody talks about.

- Source: <https://sparkco.ai/blog/mastering-langgraph-checkpointing-best-practices-for-2025>

### Comparison vs a bespoke JSON state file + JSONL recovery (the bbworkflow shape)

For a strictly **linear** pipeline (research → plan → build → validate → test → review → document) where each phase is a subprocess and "recovery" means "rerun the failed phase against the existing state dir", a JSON state file is not just adequate — it is _better_. The LangGraph checkpoint contract assumes:

- Nodes are Python callables in the same process as the orchestrator (subprocess phases violate this).
- State is a single typed dict mutated in-place by reducers (per-phase state-dirs violate this).
- The runtime owns the event loop (your `uv run` per-phase model violates this).

Where the JSON-file approach loses is _only_ when you want any of: (a) mid-flight pause/resume on human input, (b) speculative forking from a prior phase to A/B test prompts, (c) shared horizontal workers picking up the same run. None of those apply to a single-operator SDLC orchestrator. **Verdict: LangGraph checkpointing is a poor fit for bbworkflow's existing shape; the right place to steal an idea is the _strict-msgpack hardening_ mindset for any pickled artifact you persist.**

---

## 2. Multi-Agent Topologies That Actually Pay Off

Five named patterns dominate 2026 production: **fan-out, pipeline, debate, supervisor, swarm**. The honest ranking:

**Supervisor (load-bearing).** One LLM whose only job is routing — `analyst | coder | reviewer | done`. Beats swarm on accuracy because routing has a dedicated prompt and a focused decision surface. Anthropic's Claude Code, OpenAI Agents SDK handoffs, and `langgraph-supervisor-py` have all converged on this. Hierarchical = supervisor-of-supervisors, each managing a sub-pool; this is the topology behind enterprise deep-research deployments.

- Earns complexity when: you have ≥3 specialist roles and the routing decision is non-trivial.
- Source: <https://github.com/langchain-ai/langgraph-supervisor-py>, <https://www.langchain.com/blog/langgraph-multi-agent-workflows>

**Swarm / handoff (situational).** No central router; each agent has tool-call handoffs like `transfer_to_billing`. Faster (skips a routing LLM call) but harder to debug; `langgraph-swarm-py` requires a checkpointer or it forgets which agent was last active mid-conversation. Use when latency matters more than routing accuracy _and_ the handoff topology is small (~3-5 agents).

- Source: <https://github.com/langchain-ai/langgraph-swarm-py>

**Plan-and-execute (load-bearing for long-horizon tasks).** Plan node emits a list of steps; executor walks them; replanner runs after each step and can revise the remaining plan. This is the topology behind `open_deep_research`'s legacy `graph.py` and most "research agent" implementations. The non-obvious win: the replanner makes the plan _adaptive_ without making the planner _recursive_, which keeps the state graph finite and debuggable.

- Source: <https://github.com/langchain-ai/open_deep_research> (see `src/legacy/graph.py`)

**Reflection / Reflexion (load-bearing for quality, demo-ware for speed).** Generate → critique → revise loop. Cheap version (Reflection) uses a second LLM as critic; expensive version (Reflexion paper) accumulates verbal self-reinforcement across episodes. `langgraph-reflection` ships the generic harness — main agent and critique agent loop until critique returns "no issues." In practice: pays off in codegen and report writing; pure waste in tool-use loops where the environment already provides feedback (lint errors, test failures).

- Source: <https://github.com/langchain-ai/langgraph-reflection>, <https://www.langchain.com/blog/reflection-agents>

**ReAct (still useful, no longer cutting-edge).** `create_react_agent` is the LangGraph one-liner — interleave reasoning + tool calls until done. Now the _baseline_ you ablate against, not the architecture.

**Network / debate (demo-ware mostly).** All-to-all agent comms or multi-agent debate. Cited a lot, deployed rarely; the routing cost grows quadratically and the win over supervisor + reflection is unconvincing outside research papers.

### Topologies that show top-1% chops on a resume

A supervisor with sub-supervisors (hierarchical), a plan-and-execute graph with mid-flight `interrupt()` for plan approval, and a reflection loop with a _retry budget_ on the critic. If you can also point to a swarm with custom handoff tools that survived a postmortem (handoff loop, lost-agent-identity bug), you're signaling field experience over tutorial experience.

---

## 3. Human-in-the-Loop Primitives

`interrupt()` + `Command(resume=...)` is _the_ primitive. Call `interrupt(payload)` inside any node; the graph halts, the payload surfaces to the caller, and a subsequent `graph.invoke(Command(resume=value), config)` rehydrates state from the checkpoint and feeds `value` as the `interrupt()` return value.

Concrete patterns that survive production:

**Approve-irreversible-only.** Wrap `interrupt()` only around tool calls whose blast radius is high: send-email, charge-card, file-deletion, prod-deploy. Anthropic Deep Agents exposes this as an `interrupt_on=[...]` tool whitelist. Cheap reads and dry-runs do _not_ interrupt. The anti-pattern is "approval on every node" — that's not HITL, that's a chat UI with extra steps.

- Source: <https://docs.langchain.com/oss/python/deepagents/human-in-the-loop>, <https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt>

**Plan-review breakpoint.** Static interrupt placed _after_ the plan node, _before_ the executor. Human edits the plan; resume continues. This is how all the durable "agent-with-approval" UXs work; it composes cleanly with plan-and-execute.

**Dynamic interrupts for ambiguity escalation.** Conditional `interrupt()` inside the routing node: if classifier confidence < 0.7, halt and ask the human to pick. Beats false-confidence routing in customer-support and triage flows.

**Hard requirement: checkpointer must be wired.** Without a checkpointer, `interrupt()` cannot persist the pause; the entire pattern is structurally broken. Single most common HITL bug: dev tested with `InMemorySaver`, production deployed with no saver at all.

### How to wire approval without breaking automation

Two-channel design: (a) the orchestrator emits the interrupt payload to a queue (Slack DM, web UI, SQS), (b) a separate consumer translates human response into `Command(resume=...)` and calls back into the graph. Do _not_ hold the HTTP request open across the pause — return the `thread_id` and have the human-side flow re-invoke. This is the same lesson as async checkpointing: any pattern that holds a connection across an LLM-latency boundary is a production outage waiting to happen.

---

## 4. The 2025-2026 Frontier (Stuff Worth Learning Right Now)

**Deep Research agents — `open_deep_research`.** MIT-licensed, ranked #6 on Deep Research Bench. Two architectures in one repo: a `legacy/graph.py` plan-and-execute workflow with HITL planning, and the current supervisor/researcher multi-agent topology in `src/open_deep_research/deep_researcher.py`. The supervisor fans research questions to parallel researcher subagents; each researcher does its own search/summarize loop; results are compressed and passed to a separate report-writer LLM. Three things worth stealing: (a) **independent model selection per role** (summarizer ≠ researcher ≠ writer ≠ compressor) controlled by `configuration.py`, (b) **pluggable search backends** via MCP (Tavily, OpenAI native search, Anthropic native search), (c) **compression pass** before the writer — research output is verbose; the writer needs distilled inputs or it'll lose the thread.

- Source: <https://github.com/langchain-ai/open_deep_research>, <https://github.com/langchain-ai/deep_research_from_scratch> (the from-scratch tutorial walkthrough)

**Deep Agents (`langchain-ai/deepagents`, v0.5).** The "Claude Code, but for any model" harness. Bundles four things: (1) a virtual filesystem with `ls/read/write/edit/glob/grep` tools to offload large artifacts out of the context window, (2) subagents with isolated context, (3) async subagents (v0.5) that return a task ID and run remotely — main agent doesn't block, (4) a planner. This is the open-source codification of the "context engineering" pattern Anthropic has been pushing. Worth studying for the _virtual filesystem_ primitive alone — it's the cleanest answer in the ecosystem to "my agent runs out of context on real tasks."

- Source: <https://github.com/langchain-ai/deepagents>, <https://www.langchain.com/blog/deep-agents-v0-5>

**Agentic RAG with self-correction.** Three named patterns, in increasing complexity:

- **CRAG (Corrective RAG)**: retrieve → grade → if irrelevant, rewrite query and websearch → generate. Lightweight relevance evaluator; web fallback when vectorstore confidence drops. Cookbook: <https://github.com/langchain-ai/langgraph/blob/main/examples/rag/langgraph_crag.ipynb>
- **Self-RAG**: reflection tokens at every stage (Retrieve / ISREL / ISSUP / ISUSE) — the model decides whether to retrieve at all, whether passages are relevant, whether the answer is supported, whether it's useful. Cookbook: <https://github.com/langchain-ai/langgraph/blob/main/examples/rag/langgraph_self_rag.ipynb>
- **Adaptive RAG**: query-analysis router picks the retrieval strategy (no-retrieval, single-shot, multi-step, websearch) by query complexity. Cheapest queries skip RAG entirely. Tutorial: <https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_adaptive_rag/>

Adaptive is the production winner — most queries don't need the full grading cascade, and Adaptive routes accordingly.

**LangGraph Studio + LangGraph Platform (renamed "LangSmith Deployment" Oct 2025).** Studio is the visual graph debugger; Platform is the hosted runtime. Self-Hosted Lite is free; Cloud SaaS is the managed option; BYOC runs in your VPC; Self-Hosted Enterprise is full air-gap. Worth knowing for the architecture conversation: even if you don't deploy on Platform, the abstractions (assistants, threads, runs) define how the rest of the ecosystem thinks about agent lifecycles.

- Source: <https://www.langchain.com/blog/langgraph-platform-announce>, <https://docs.langchain.com/oss/python/langgraph/deploy>

**`langgraph-bigtool`** — for agents with hundreds of tools, indexes them in a vector store and retrieves the relevant subset per turn. Solves the "200 tools blow out the system prompt" problem cleanly. <https://github.com/langchain-ai/langgraph-bigtool>

**`langgraph-codeact`** — CodeAct architecture: instead of JSON tool calls, the agent writes Python that calls tools as functions. Fewer turns, more expressive composition. <https://github.com/langchain-ai/langgraph-codeact>

**`langmem`** — long-term agent memory: extraction, consolidation, retrieval. Pairs with the `Store` API for cross-thread memory. <https://github.com/langchain-ai/langmem>

---

## 5. Patterns Specifically Applicable to a Coding SDLC

The bbworkflow shape (research → plan → build → validate → test → review → document) maps onto several documented LangGraph patterns. Worth stealing:

**Generate → Check → Reflect (self-correcting codegen).** The canonical loop: generate code, execute it (or static-check with Pyright), if it fails, run a reflect node that takes the error + the code and produces a critique, feed back to generate. Bound the loop with a retry budget (typical: 3 attempts before escalating to human). This is the exact shape of the LangGraph self-correcting code-generation tutorial and Devin's "autofix review comments" feature.

- Source: <https://learnopencv.com/langgraph-self-correcting-agent-code-generation/>, <https://cognition.ai/blog/closing-the-agent-loop-devin-autofixes-review-comments>
- Devin quote: "The agent writes. The reviewer catches. Bot triggers fire. Fixes get applied automatically." The verification step is CI; the trigger is review-bot comments on PRs.

**Anthropic's "gather context → take action → verify work → repeat".** Anthropic's official Claude Agent SDK best-practices post names this as _the_ agent feedback loop. Verification methods, in priority order: (1) rules-based (lint, types, tests) — concrete detailed errors, (2) visual feedback (screenshots), (3) LLM-as-judge — least robust. The non-obvious lesson is the ordering: if you can verify with a linter, _never_ use an LLM judge.

- Source: <https://claude.com/blog/building-agents-with-the-claude-agent-sdk>

**Two-agent code-review (writer + reviewer).** Cognition's "one agent writes, the other pressure-tests, and this continues in a loop." Same shape as the reflection pattern but materialized as two distinct agents with different system prompts. Higher token spend; higher quality; only worth it where the verifier has genuine signal (test outcomes, lint, types) rather than "does this look good."

**Plan-act-verify with research gate.** The bbworkflow `.adw/exploit/exploit_research_gate.py` is already this — a research subroutine that interrupts the action loop to gather context before continuing. The LangGraph idiom is a conditional edge from the executor node to a research subgraph whenever a tool returns "unknown vendor" or similar low-confidence signal.

**Map-reduce for parallel codegen / parallel test runs.** The `Send` API spawns N parallel branches at runtime (`Send("test_runner", {"test_id": t}) for t in tests`), each runs an independent subgraph, results aggregate via a reducer (`operator.add` for list concat). This is genuinely better than `asyncio.gather` for any case where the parallel branches themselves need checkpointing — a flaky test runner that crashes mid-batch doesn't lose the 200 successes already in state.

- Source: <https://langchain-ai.github.io/langgraph/how-tos/map-reduce/>

---

## 6. Anti-Patterns and Where LangGraph DOESN'T Win

Be honest about the costs. LangGraph imposes:

1. **In-process Python runtime.** All nodes run in the orchestrator's interpreter. The moment your phases are isolated subprocesses (different `uv` envs, different timeouts, OS-level kill), you're fighting the framework. Subprocess pipelines are _cheaper_ to operate than in-process graphs for one reason: a `kill -9` on a runaway phase doesn't take down your scheduler.

2. **Single-typed-state assumption.** State is one `TypedDict` mutated by reducers. Real SDLC artifacts are heterogeneous and partitioned per-phase — recon outputs, test results, source diffs, review comments — and naturally live in a directory tree, not a single dict. Cramming heterogeneous artifacts into one state explodes the dict size and the checkpoint table.

3. **Determinism tax inside nodes.** Checkpoints save state _between_ nodes, never inside. If your node body has a 30-minute loop, a crash mid-loop loses all the in-loop progress. Idiomatic LangGraph fixes this by making the inner step _its own node_, which means your "single test runner" turns into a 5-node subgraph — worth it for HITL, overkill for batch.
   - Source: <https://medium.com/data-science-collective/langgraph-vs-temporal-for-ai-agents-durable-execution-architecture-beyond-for-loops-a1f640d35f02>

4. **Versioning the graph is hard.** Changing the graph structure invalidates in-flight checkpoints. You either keep old graph versions around or accept that an upgrade orphans every paused thread. Subprocess pipelines have the same problem in principle but in practice you can just `rm -rf agents/<old_id>/` and move on.

5. **It is overkill for a single agent doing a single task.** Quote: _"For a single agent doing a single task, LangGraph is overkill — a plain LangChain chain is the right answer. The break-even is somewhere around two agents, three branching decisions, or any flow where a partial run is more valuable than a failed run."_ Below that threshold, raw Anthropic SDK + a JSON state file genuinely wins.
   - Source: <https://www.prasanna.dev/posts/langgraph-patterns-and-conventions>

### Where raw Anthropic SDK + a state file beats LangGraph

- **Linear pipelines** with no branching and no HITL gate. The graph is a list — DAGs are overkill.
- **Subprocess orchestration** where phases need OS isolation, independent timeouts, or different Python envs.
- **Batch jobs** with no need for partial-progress recovery beyond "rerun the failed phase."
- **Single-shot, stateless tool-use** where the entire run completes in one LLM call + a few tool calls.
- **Operator-driven CLI tools** where the human runs each phase explicitly and inspects output between phases — the human _is_ the orchestrator.

The bbworkflow shape lives squarely in the "subprocess wins" zone. **Sharpened intuition: LangGraph is the right abstraction when the orchestrator _and_ the agents share an event loop AND you need durable pause/resume on human input. Drop either condition and a thinner abstraction is cheaper.**

### Where LangGraph + Temporal beats LangGraph alone

For genuinely long-running agentic workflows (hours-to-days, multiple external system calls, strong durability SLAs), the production pattern is **LangGraph for reasoning, Temporal for orchestration**. Temporal owns the event journal and replay; LangGraph nodes are wrapped as Temporal activities. LangGraph alone can't recover from process crashes _inside_ a node — Temporal's journal can.

- Source: <https://cordum.io/blog/temporal-vs-langgraph>, <https://agentmarketcap.ai/blog/2026/04/08/langgraph-vs-temporal-long-running-agent-workflows-2026>

---

## 7. Career-Relevant Signaling (Top-1% Artifacts)

What to actually ship to demonstrate the chops, in rough order of signal strength:

1. **A Deep Research agent over your company's knowledge base.** Fork `open_deep_research`, swap Tavily for your internal search MCP server, swap the writer model for whatever your enterprise allows, deploy on Self-Hosted Lite. This is the single most legible 2026 artifact — it touches multi-agent (supervisor + researchers), MCP, model-per-role configuration, parallel research, and a real deliverable. The corp-deployable version of "I built a Devin." <https://github.com/langchain-ai/open_deep_research>

2. **A supervisor-with-HITL plan-approval graph that has survived an incident postmortem.** Concretely: hierarchical supervisor, plan-and-execute under each leaf, `interrupt()` for plan approval and for high-blast-radius tools, Postgres checkpointer, a real two-channel approval UX (not a synchronous blocking call). The signal here is operational maturity, not feature count.

3. **A self-correcting codegen loop with a retry budget and rules-based verifier.** Generate → run-tests → reflect → regenerate, max 3 attempts, escalate to human on bust. Bonus signal if the verifier is _not_ an LLM (lint, types, tests). This separates "I read the Reflexion paper" from "I shipped Reflexion."

4. **A LangGraph + Temporal hybrid.** Most engineers don't even know this is a thing. Wrap your LangGraph graph as a Temporal workflow, with nodes as activities; show you understand the determinism boundary. Demonstrates that you can pick the right abstraction at the right layer.

5. **A `Store`-backed long-term-memory pattern with `langmem`.** Cross-thread memory, extraction + consolidation, retrieval. The artifact is short but it signals you've moved past "agent = stateless prompt + tools."

6. **A `langgraph-bigtool` or `langgraph-codeact` integration.** Niche but high-signal — the engineers who reach for these have actually hit the "200 tools" or "JSON tool-call ceiling" problems in production.

Things that are _not_ differentiating in 2026: ReAct agents, basic RAG, single-agent tool-use, "I built a chatbot with memory." Table stakes.

### What demonstrates top-1% in an enterprise context

Frame the artifact in terms of **what production problem it solved**, not what framework feature it used. "We replaced our N-step manual research handoff with a deep-research agent that runs in <thread minutes> and our analysts rated <X%> of reports as ready-to-publish" beats "I built a supervisor-router graph with PostgresSaver." The framework knowledge is the entry ticket; the production framing is the differentiator.

---

## Quick Reference — Repo URLs Worth Bookmarking

| Repo                                                         | What it is                                            |
| ------------------------------------------------------------ | ----------------------------------------------------- |
| <https://github.com/langchain-ai/langgraph>                  | Core                                                  |
| <https://github.com/langchain-ai/open_deep_research>         | Deep research agent (#6 on Deep Research Bench)       |
| <https://github.com/langchain-ai/deep_research_from_scratch> | Tutorial walkthrough of the above                     |
| <https://github.com/langchain-ai/deepagents>                 | Claude-Code-style harness with virtual FS + subagents |
| <https://github.com/langchain-ai/langgraph-supervisor-py>    | Supervisor topology helper                            |
| <https://github.com/langchain-ai/langgraph-swarm-py>         | Swarm / handoff topology helper                       |
| <https://github.com/langchain-ai/langgraph-reflection>       | Reflection / critique loop harness                    |
| <https://github.com/langchain-ai/langgraph-bigtool`          | Vector-store-backed tool retrieval                    |
| <https://github.com/langchain-ai/langgraph-codeact>          | CodeAct (code-as-tool-calls) architecture             |
| <https://github.com/langchain-ai/langmem>                    | Long-term agent memory                                |
| <https://github.com/von-development/awesome-LangGraph>       | Curated ecosystem index                               |

## Sources (full list)

- LangChain — _Making it easier to build human-in-the-loop agents with interrupt_: <https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt>
- LangChain — _Reflection Agents_: <https://www.langchain.com/blog/reflection-agents>
- LangChain — _Self-Reflective RAG with LangGraph_: <https://www.langchain.com/blog/agentic-rag-with-langgraph>
- LangChain — _LangGraph Platform announce_: <https://www.langchain.com/blog/langgraph-platform-announce>
- LangChain — _Deep Agents v0.5_: <https://www.langchain.com/blog/deep-agents-v0-5>
- LangChain docs — _Durable execution_: <https://docs.langchain.com/oss/python/langgraph/durable-execution>
- LangChain docs — _Time-travel_: <https://docs.langchain.com/oss/python/langgraph/use-time-travel>
- LangChain docs — _HITL for Deep Agents_: <https://docs.langchain.com/oss/python/deepagents/human-in-the-loop>
- LangGraph adaptive-RAG tutorial: <https://langchain-ai.github.io/langgraph/tutorials/rag/langgraph_adaptive_rag/>
- LangGraph CRAG cookbook: <https://github.com/langchain-ai/langgraph/blob/main/examples/rag/langgraph_crag.ipynb>
- LangGraph Self-RAG cookbook: <https://github.com/langchain-ai/langgraph/blob/main/examples/rag/langgraph_self_rag.ipynb>
- Cognition — _Closing the Agent Loop: Devin Autofixes Review Comments_: <https://cognition.ai/blog/closing-the-agent-loop-devin-autofixes-review-comments>
- Anthropic — _Building agents with the Claude Agent SDK_: <https://claude.com/blog/building-agents-with-the-claude-agent-sdk>
- Sparkco — _Mastering LangGraph Checkpointing_: <https://sparkco.ai/blog/mastering-langgraph-checkpointing-best-practices-for-2025>
- Cordum — _Temporal vs LangGraph (2026)_: <https://cordum.io/blog/temporal-vs-langgraph>
- AgentMarketCap — _LangGraph vs Temporal_: <https://agentmarketcap.ai/blog/2026/04/08/langgraph-vs-temporal-long-running-agent-workflows-2026>
- Prasanna — _LangGraph patterns and conventions_: <https://www.prasanna.dev/posts/langgraph-patterns-and-conventions>
- Atomic Object — _LangGraph execution model is trickier than you might think_: <https://spin.atomicobject.com/langgraphs-execution-model-tricky/>
- AI Practitioner — _Scaling LangGraph: Parallelization, Subgraphs, Map-Reduce_: <https://aipractitioner.substack.com/p/scaling-langgraph-agents-parallelization>
- Focused — _Multi-Agent Orchestration in LangGraph: Supervisor vs Swarm_: <https://focused.io/lab/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture>
- Medium / Data Science Collective — _LangGraph vs Temporal for AI Agents_: <https://medium.com/data-science-collective/langgraph-vs-temporal-for-ai-agents-durable-execution-architecture-beyond-for-loops-a1f640d35f02>
- LearnOpenCV — _LangGraph: Building self-correcting RAG agent_: <https://learnopencv.com/langgraph-self-correcting-agent-code-generation/>
- LangGraph time-travel concept: <https://langchain-ai.github.io/langgraph/concepts/time-travel/>
