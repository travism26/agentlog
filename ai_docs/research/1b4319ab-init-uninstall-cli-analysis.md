# Research: `agentlog init` / `agentlog uninstall` CLI Implementation

## Metadata

adw_id: `1b4319ab`
prompt: `/tmp/agentlog_init_prompt.md` — implement v0.1 ship-scope item #1: the `agentlog init` and `agentlog uninstall` subcommands that manage Claude Code hook registrations in `settings.json`.
date: `2026-05-27`

## Executive Summary

The repo is a clean pre-implementation scaffold: a single `cli.py` stub registers the six v0.1 subcommands (`init`, `uninstall`, `tail`, `ls`, `cost`, `view`) and routes them all to a `_not_implemented` placeholder that exits 2. Implementing `init`/`uninstall` is self-contained — it only needs to (a) replace those two stubs in `cli.py`, (b) add a new module (most natural: `src/agentlog/hooks_install.py`) that knows how to read/merge/write Claude Code `settings.json`, and (c) update `tests/test_cli_smoke.py` so it no longer asserts "not yet implemented" for those two commands. No runtime dependencies (stdlib only) and no hook handler logic are in scope — handler entries can point to a placeholder `agentlog _hook <hook-name>` stub.

## Existing Architecture

### Relevant Documentation Found

- `DESIGN.md` (root) — the locked v0.1 design. The Hook integration section (table mapping `SessionStart`/`UserPromptSubmit`/`PostToolUse`/`Stop`/`SessionEnd` to handler actions) and the "Installation surface" snippet (`agentlog init`, `--project`, `--dry-run`, `agentlog uninstall`) define this task verbatim. The `PreToolUse` exclusion is restated here and in CLAUDE.md.
- `CLAUDE.md` (root) — the hard rules. Most load-bearing for this task: rule #3 (never auto-install during `pip install`), rule #8 (`init` must preserve existing hooks; idempotent), and the implicit fail-clean rule (malformed `settings.json` → clean non-zero exit, not silent overwrite).
- `research/langgraph_patterns_2026.md` — not directly relevant to install logic, but reinforces "stdlib-only, sidecar-first" framing.
- `research/ai_dev_pain_points_2026.md` — reinforces the trust thesis: a botched `init` that mangles `settings.json` is the single most adoption-killing failure mode.
- `.claude/commands/` — local slash-command definitions (`feature.md`, `implement.md`, `research.md`, `validate.md`, etc.) — relevant only because they show this repo runs an ADW pipeline (research → plan → implement → test → validate); naming and structure of the output research doc should fit that flow.
- `.adw/adw_modules/` — ADW orchestration internals (`agent.py`, `state.py`, `utils.py`). Not touched by this task. `utils.py::setup_logger` is the one piece DESIGN.md flags as the eventual seed for `agentlog/log.py`, but logging is out of scope for `init`/`uninstall`.

### Component Map

```
src/agentlog/
├── __init__.py         # exports __version__
├── __main__.py         # `python -m agentlog` shim → cli.main()
├── cli.py              # argparse scaffold; SUBCOMMANDS tuple; _not_implemented stub
└── (new) hooks_install.py   # NEW: read/merge/write settings.json; idempotent install + uninstall

tests/
├── __init__.py
└── test_cli_smoke.py   # parametrized over SUBCOMMANDS — currently asserts rc==2 + "not yet implemented"
                        # MUST narrow the parametrization (or split tests) once init/uninstall are real
```

Hooks live in Claude Code's `settings.json`. From DESIGN.md and the well-documented Claude Code hooks API, the shape is:

```json
{
  "hooks": {
    "SessionStart":     [ { "hooks": [ { "type": "command", "command": "<shell>" } ] } ],
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "<shell>" } ] } ],
    "PostToolUse":      [ { "matcher": "*", "hooks": [ { "type": "command", "command": "<shell>" } ] } ],
    "Stop":             [ { "hooks": [ { "type": "command", "command": "<shell>" } ] } ],
    "SessionEnd":       [ { "hooks": [ { "type": "command", "command": "<shell>" } ] } ]
  }
}
```

The merge target is `~/.claude/settings.json` (user scope) or `./.claude/settings.json` (project scope under `--project`).

### Key Files and Modules

| Path | Purpose for this task |
|------|----------------------|
| `src/agentlog/cli.py` | Stub `init`/`uninstall` subparsers live here. Replace their `_not_implemented` `set_defaults(func=…)` with real handlers that take `--project` and `--dry-run`. |
| `src/agentlog/__init__.py` | Defines `__version__`; nothing to change. |
| `tests/test_cli_smoke.py` | Currently parametrizes over the full `SUBCOMMANDS` tuple asserting rc==2 + "not yet implemented". Must be updated so `init`/`uninstall` are no longer in the "not implemented" parametrization, and new dedicated tests are added. |
| `pyproject.toml` | Already pins stdlib-only core deps. Do not add a JSON-merge library — use `json` from stdlib. |
| `DESIGN.md` "Hook integration" + "Installation surface" sections | Specs to satisfy. |
| `CLAUDE.md` hard rules #3 and #8 | Acceptance gates. |

## Affected Areas

### Files That Will Need Changes

| File | Change |
|------|--------|
| `src/agentlog/cli.py` | (1) Add per-subcommand argparse setup for `init` and `uninstall` so they accept `--project` and `--dry-run`. The current loop sets every subcommand to `_not_implemented`; that loop must skip `init`/`uninstall` or set them up separately. (2) Wire them to the new module's entry functions. |
| `src/agentlog/hooks_install.py` (NEW) | Core logic: resolve target path, load existing settings (or `{}`), merge agentlog hook entries, write atomically. Symmetric `uninstall` that removes only agentlog-tagged entries. Provide a `--dry-run` mode that returns/prints the unified diff but does not write. |
| `tests/test_cli_smoke.py` | Exclude `init`/`uninstall` from the "not yet implemented" parametrization (e.g., parametrize over `[c for c in SUBCOMMANDS if c not in {"init", "uninstall"}]`). |
| `tests/test_hooks_install.py` (NEW) | Dedicated coverage: dry-run, fresh install, idempotent re-install, merge with existing hooks, uninstall preserving foreign hooks, malformed-JSON error path, `--project` writes to `./.claude/settings.json` and creates parent dirs. |

### Dependencies

- **What this code will depend on**: stdlib only (`argparse`, `json`, `pathlib`, `sys`, optionally `difflib` for the dry-run diff, optionally `tempfile`+`os.replace` for atomic write).
- **What depends on this code**:
  - Future hook-handler implementation (ship-scope item #2) — the placeholder command strings written by `init` (e.g. `agentlog _hook SessionStart`) become the integration point. Choose a command shape now that the future handler can keep without rewriting users' `settings.json`.
  - `tests/test_cli_smoke.py` — as above, must be updated.
  - The README quickstart and demo gif (ship-scope item #7) will demonstrate `agentlog init` first, so its output text (dry-run diff format, success message) is user-facing.

### Integration Points

1. **Claude Code's hook contract** — `init` writes JSON that Claude Code reads. The shape above (`hooks.<EventName>[].hooks[]` with `type: "command"` and a `command` string, plus optional `matcher`) is the integration surface. Anthropic owns this schema; CLAUDE.md hard rule #7 ("Anthropic changes hook payload schema → log warning, don't crash") applies to handlers, but for `init` the dual is: be conservative about what we write (only the documented fields). DESIGN.md "Risks" table line 7 reinforces this.
2. **Filesystem** — user-scoped `~/.claude/settings.json` vs project-scoped `./.claude/settings.json`. Both may not exist yet; both may already contain hooks from other tools. Need atomic write (`os.replace`) so a crash mid-write doesn't leave a half-written file.
3. **Future `_hook` subcommand** — `init` writes commands that point at `agentlog _hook <name>`. That subcommand is not in `SUBCOMMANDS` yet. Either (a) add a hidden `_hook` subcommand stub now that exits 0, or (b) document that `init` writes a forward-reference and the v0.1 handler PR will fill it in. The prompt explicitly says option (b) is acceptable: "the handler entries can point to a placeholder command like `agentlog _hook <hook-name>` which can be a stub that exits 0. Document this clearly."

## Impact Analysis

### Scope of Change

Small and localized. One new module (~150–250 LOC), one stub-replacement in `cli.py`, one new test file, one edit to the existing smoke test. No changes to packaging, dependencies, docs (beyond a brief note in the README quickstart later), or any other source file. Risk surface is the JSON-merge logic itself, which is why test coverage of the merge/preserve/uninstall cases is the acceptance bar.

### Risks and Considerations

1. **Identifying "our" hook entries.** The prompt calls this out explicitly. Three candidate strategies, with tradeoffs:
   - **(a) Sentinel marker in the `command` string.** Embed an unambiguous token like `agentlog _hook` (the actual command we run) or an explicit comment marker `# agentlog-managed`. Simple, no schema additions, survives JSON round-trips, robustly identifiable. **Recommended** — it doubles as the placeholder command from the prompt.
   - **(b) Side-file inventory** (`~/.agentlog/installed_hooks.json`). Decouples ownership from the command string, but introduces a second source of truth that can drift if the user hand-edits `settings.json`. Adds a cleanup failure mode.
   - **(c) Custom JSON wrapper key** (e.g., `{"_agentlog": true, "type": "command", ...}`). Cleanest in theory, but pollutes Claude Code's schema with a field it doesn't recognize — a future Anthropic change to schema validation could break installs. Riskier than (a).

   Strategy (a) — sentinel-via-command — is the best fit. Each hook entry's `command` is something like `agentlog _hook SessionStart` (or with an absolute path resolved from `sys.executable`/`shutil.which("agentlog")`). The `agentlog` token plus `_hook` is the identifier; uninstall removes any entry whose `command` starts with `agentlog _hook ` (or equivalent absolute form).

2. **Atomicity of writes.** Mid-write crash leaves users with corrupted `settings.json`. Mitigation: write to a sibling temp file in the same directory, then `os.replace()`.

3. **Permission errors.** `~/.claude/settings.json` may be owned/locked. Surface a clean error and exit non-zero. Do not retry.

4. **Malformed existing settings.** Per CLAUDE.md, exit non-zero with a clear error — do NOT silently overwrite. Detection is `json.JSONDecodeError` from `json.load`. Print the offending path and a hint ("backup and re-run, or hand-edit").

5. **Project scope and missing dirs.** `agentlog init --project` should create `./.claude/` if it doesn't exist (mkdir parents=True, exist_ok=True). Settings.json is created if absent.

6. **Idempotency edge cases.** Re-running install with an existing agentlog entry whose `command` string has drifted (e.g., user moved their venv, so the absolute path changed). Two valid options: (i) treat any `agentlog _hook <name>` entry as "ours" and leave it alone if present, or (ii) overwrite to the canonical form. Option (i) is safer (respects user edits); option (ii) is more predictable. **Recommend (i)** — match by sentinel, leave content untouched if a matching event already has an agentlog entry. Document the trade-off.

7. **Multiple agentlog installs / stale entries.** If two agentlog versions installed the same hook with different command paths, both sentinel entries match. `uninstall` should remove ALL agentlog-tagged entries; `init` should de-dupe by event (one agentlog entry per event after running).

8. **Empty `hooks.<Event>` arrays after uninstall.** If removing the agentlog entry leaves an empty list, drop the empty list entirely so we don't litter user's settings.json with `"SessionStart": []`. Same for the top-level `hooks` key if it ends up empty.

9. **`PreToolUse`.** Hard rule #5: do NOT register it. The `EVENTS` tuple in the new module must be exactly the five from DESIGN.md. Add a comment so future contributors don't "helpfully" extend it.

10. **Dry-run output format.** The prompt says "print the exact diff that would be applied." Use `difflib.unified_diff` over before/after pretty-printed JSON — that's stdlib, readable, and unambiguous about what's changing.

### Existing Patterns to Follow

- **CLI structure** — `cli.py` already follows `argparse` with subparsers and a `func=` per subcommand. Stay consistent: handlers take `args: argparse.Namespace` and return `int`. `main()` casts to `int` and returns.
- **`from __future__ import annotations` + explicit return types** — used in `cli.py` and `test_cli_smoke.py`. Apply the same to new code; mypy strict is on (`pyproject.toml`).
- **stdlib only** — `pyproject.toml` line 28 (`dependencies = []`). No new deps for `init`/`uninstall`.
- **Ruff line-length 100, target-version py311**. Use modern syntax (PEP 604 unions, `pathlib.Path`).
- **Test style** — `pytest` with `capsys` capture; `parametrize` for table-driven cases. Use `tmp_path` for filesystem tests so each runs in isolation; never touch real `~/.claude/`.

## Recommendations

1. **Module layout**: create `src/agentlog/hooks_install.py` with the following surface:
   - `EVENTS: tuple[str, ...] = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")` — single source of truth; explicit comment noting `PreToolUse` is deferred per CLAUDE.md hard rule #5.
   - `HOOK_COMMAND_PREFIX = "agentlog _hook"` — the sentinel.
   - `resolve_settings_path(project: bool) -> Path` — returns `~/.claude/settings.json` or `<cwd>/.claude/settings.json`.
   - `load_settings(path: Path) -> dict` — returns `{}` if file missing; raises a typed error (e.g., custom `MalformedSettingsError`) on `JSONDecodeError`.
   - `plan_install(existing: dict) -> dict` — pure function returning the new settings dict; idempotent.
   - `plan_uninstall(existing: dict) -> dict` — pure function removing any hook entry whose command starts with the sentinel; drops emptied event arrays and the `hooks` key if it ends up empty.
   - `diff(before: dict, after: dict) -> str` — `difflib.unified_diff` of pretty-printed JSON.
   - `write_atomic(path: Path, data: dict) -> None` — temp file + `os.replace`.
   - `run_init(*, project: bool, dry_run: bool) -> int` / `run_uninstall(...)` — orchestration that the CLI handlers call.

2. **CLI wiring** in `cli.py`:
   - Stop using the single `_not_implemented` loop for `init`/`uninstall`. Either: factor a `_register_init` / `_register_uninstall` helper that adds `--project` and `--dry-run` to their subparsers and sets `func=` to the new handlers; or keep the loop and override after.
   - Keep `SUBCOMMANDS` intact (tests still iterate it).

3. **Test plan** (matches prompt acceptance criteria):
   - `test_init_dry_run_prints_diff_and_writes_nothing` — uses `tmp_path` + monkeypatched `HOME`/cwd.
   - `test_init_fresh_install_creates_file` — `~/.claude/settings.json` did not exist.
   - `test_init_is_idempotent` — run twice, byte-compare result.
   - `test_init_preserves_existing_user_hooks` — pre-seed settings.json with foreign hooks, assert they're still present after install.
   - `test_uninstall_removes_only_agentlog_entries` — pre-seed with a mix of agentlog + foreign entries; assert foreign entries survive.
   - `test_uninstall_after_init_yields_original_settings` — round-trip property: pre-existing settings unchanged.
   - `test_malformed_settings_exits_nonzero` — write garbage JSON, assert clean error + non-zero rc, no overwrite.
   - `test_project_scope_creates_dot_claude_dir` — verify `./.claude/settings.json` written under `tmp_path` cwd.
   - Update `test_cli_smoke.py::test_subcommands_registered_but_not_implemented` to parametrize over `[c for c in SUBCOMMANDS if c not in {"init", "uninstall"}]`.

4. **Forward-compatibility note in code**: when item #2 (the actual hook handlers) lands, the handler-runtime PR should be able to keep the same command string `agentlog _hook <Event>`. To make that frictionless, add a hidden `_hook` subparser stub in `cli.py` now that exits 0 (truly fail-open) and is omitted from the help/`SUBCOMMANDS` tuple — that way `agentlog init` produces a `settings.json` that actually runs against the current build without 127-erroring on hook invocation. This is a small, optional addition that hardens the demo path; if scoped out, document it as a follow-up.

5. **Do NOT**:
   - Touch `~/.claude/settings.json` from any test.
   - Add a JSON-merge library.
   - Register `PreToolUse`.
   - Auto-create `~/.agentlog/` from `init` (out of scope; that's the handler runtime's job).
   - Use `--amend` or any destructive git op when committing.
