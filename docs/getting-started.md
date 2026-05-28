# Getting Started

If you've read the README and want a 5-minute hands-on tour, this is the page. It covers both ingest paths (hooks mode for interactive `claude` sessions, SDK mode for scripted runs), the cost and view commands, and where everything lands on disk.

---

## 1. Install

v0.1 isn't on PyPI yet. Install from the GitHub repo:

```bash
pip install 'agentlog[tui] @ git+https://github.com/travism26/agentlog'
```

or via uv:

```bash
uv tool install --with rich 'git+https://github.com/travism26/agentlog'
```

The `[tui]` extra installs `rich`, which is required only for `agentlog view` (the three-panel TUI renderer). All other subcommands — `init`, `uninstall`, `tail`, `ls`, `cost` — are stdlib-only and work without it. `agentlog view --json` also works without `rich`.

A PyPI release is planned for v0.2; once published, the install will simplify to `pip install 'agentlog[tui]'`.

Verify the install:

```bash
agentlog --version
```

---

## 2. Capture interactive sessions (hooks mode)

### Register the hooks

```bash
agentlog init
```

This merges five hook entries into `~/.claude/settings.json`. Each entry routes a Claude Code lifecycle event (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`) to `agentlog _hook <Event>`. Existing hooks are preserved; the operation is idempotent.

To scope the install to the current project instead:

```bash
agentlog init --project
```

Preview what would change without writing:

```bash
agentlog init --dry-run
```

### Run a session

Start a normal Claude Code session as you would without agentlog:

```bash
claude
```

agentlog hooks run in Claude Code's hot path. They write to `~/.agentlog/runs/<session_id>/` with no user-visible side effects and no network calls.

### Verify capture

```bash
agentlog ls
```

Expected output (columns: `RUN ID`, `SOURCE`, `STARTED`, `DUR`, `EVENTS`, `TOKENS`, `MODEL`):

```
RUN ID                SOURCE  STARTED              DUR   EVENTS  TOKENS   MODEL
--------------------  ------  -------------------  ----  ------  -------  ----------------------
abc1234...            hooks   2026-05-28T14:32:00Z  4m3s  47      12,543   claude-sonnet-4-6
```

---

## 3. Capture scripted runs (SDK mode)

If you have `cc_raw_output.jsonl` files from a `claude-code-sdk` or Anthropic SDK scripted run, `agentlog tail` ingests them into the same `runs/<id>/` layout.

### Single file

```bash
agentlog tail ./logs/cc_raw_output.jsonl
```

### Recursive directory walk

```bash
agentlog tail ./logs/
```

`tail` recurses up to 5 levels deep and processes every `cc_raw_output.jsonl` it finds. Already-ingested files are skipped by default (idempotent). Use `--force` to re-ingest:

```bash
agentlog tail ./logs/ --force
```

Dry-run to preview what would be written:

```bash
agentlog tail ./logs/ --dry-run
```

Pin an explicit run ID (single-file only):

```bash
agentlog tail ./logs/cc_raw_output.jsonl --run-id my-run-2026-05-28
```

After ingestion, `agentlog ls` shows the run with `SOURCE=sdk` alongside any hooks runs.

---

## 4. See what you spent

Single-run breakdown:

```bash
agentlog cost sdk-abc123
```

Sample output:

```
Run:      sdk-abc123
Source:   sdk
Model:    claude-sonnet-4-6
Started:  2026-05-28T14:32:00Z
Duration: 4m3s

              Tokens  Rate (per 1M)    Cost
------------  ------  -------------  ------
Input          8,120       $3.00/1M  $0.0244
Output         3,211      $15.00/1M  $0.0482
Cache read       891       $0.30/1M  $0.0003
Cache create     321       $3.75/1M  $0.0012
------------  ------  -------------  ------
Total         12,543                 $0.0740

pricing snapshot: built-in (2026-05-27). Override with --pricing <file> or $AGENTLOG_PRICING.
```

Cross-run rollup sorted by cost descending:

```bash
agentlog cost --all
```

Filter to the last 7 days:

```bash
agentlog cost --all --since 7d
```

**Pricing caveat:** the built-in pricing snapshot is dated 2026-05-27. Anthropic changes prices over time. To use updated rates, supply a JSON override file:

```bash
agentlog cost --all --pricing ~/my-pricing.json
```

or set `$AGENTLOG_PRICING` to the file path. The file format is `{"model-id": {"input": X, "output": Y, "cache_read": Z, "cache_creation": W}}` where values are USD per million tokens. See [cli-reference.md — agentlog cost](cli-reference.md#agentlog-cost) and [architecture.md — The pricing table](architecture.md#the-pricing-table) for details.

---

## 5. See what they did

```bash
agentlog view sdk-abc123
```

Requires `rich` (installed via the `[tui]` extra — see [Install](#1-install) above). Renders a three-panel layout: **header** (run metadata), **timeline** (chronological event list with ASCII rail decorations, color-coded by event kind), and **cost footer** (token counts and dollar cost). See the README for a hero screenshot of the TUI output.

Useful flags:

```bash
# Limit to first 20 events
agentlog view sdk-abc123 --limit 20

# Events only — skip header and cost footer
agentlog view sdk-abc123 --events-only

# Disable 80/60-char per-row display truncation
agentlog view sdk-abc123 --no-truncate

# JSON output — works without rich installed
agentlog view sdk-abc123 --json
```

See [cli-reference.md — agentlog view](cli-reference.md#agentlog-view) for the full flag reference.

---

## 6. Uninstalling

```bash
agentlog uninstall
```

Removes only the agentlog-tagged hook entries (those whose command string starts with `agentlog _hook`). Hooks you added manually are preserved. The command is idempotent and exits 0 if no agentlog hooks are found.

For project-scoped settings:

```bash
agentlog uninstall --project
```

Preview before writing:

```bash
agentlog uninstall --dry-run
```

---

## 7. Where things live

| Path | Contents |
|---|---|
| `~/.agentlog/runs/<id>/state.json` | Run metadata: session ID, model, started/ended timestamps, CWD, event count, source (`hooks` or `sdk`) |
| `~/.agentlog/runs/<id>/events.jsonl` | Append-only event log; one JSON record per line; each record carries `schema_version`, `event`, `timestamp`, `session_id`, `source` |
| `~/.agentlog/runs/<id>/cost.json` | Token totals (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`) |
| `~/.agentlog/index.sqlite3` | SQLite index for fast `agentlog ls` queries — a derived cache, not the source of truth |
| `~/.agentlog/_self.log` | Internal error log for the hook fail-open boundary; useful when debugging a silent capture failure |
| `~/.agentlog/pricing.json` | Optional user-managed pricing override |

Override the data root with `AGENTLOG_HOME=<path>` (all commands honor this env var).

See [architecture.md](architecture.md) for the full schema field listing, the SQLite bootstrap order, and the tail translator's timestamp-derivation logic.

---

## Next steps

- [docs/cli-reference.md](cli-reference.md) — per-flag, per-exit-code reference for every subcommand
- [docs/architecture.md](architecture.md) — two ingest paths, on-disk layout, hook perf contract, SQLite index, pricing table
- [DESIGN.md](../DESIGN.md) — locked v0.1 design: problem statement, architecture rationale, ship scope, explicit non-goals
- [GitHub issue tracker](https://github.com/travism26/agentlog/issues) — bug reports and feature requests
