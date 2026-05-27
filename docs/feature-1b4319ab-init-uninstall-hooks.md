# `agentlog init` / `agentlog uninstall` — Claude Code Hook Registration

**ADW ID:** `1b4319ab`
**Date:** 2026-05-27
**Specification:** `specs/feature-1b4319ab-init-uninstall-hooks.md`

## Overview

Implements v0.1 ship-scope item #1: two CLI subcommands that manage agentlog's Claude Code hook registrations in `settings.json`. `agentlog init` merges agentlog's five hook event handlers into Claude Code's `settings.json` without disturbing any existing hooks; `agentlog uninstall` symmetrically removes only agentlog-tagged entries. Both commands are explicit, opt-in, idempotent, stdlib-only, and fail cleanly on malformed input.

## What Was Built

- `src/agentlog/hooks_install.py` — new stdlib-only module with pure plan/diff functions and I/O orchestrators
- `src/agentlog/cli.py` — `init` and `uninstall` subcommands wired to real handlers with `--project` and `--dry-run` flags
- Hidden `_hook <event>` no-op subparser so a freshly installed `settings.json` runs cleanly against this build (exits 0)
- `tests/test_hooks_install.py` — 20 unit and integration tests covering all acceptance criteria
- Updated `tests/test_cli_smoke.py` — excludes `init`/`uninstall` from the "not yet implemented" parametrization

## Technical Implementation

### Files Modified

- `src/agentlog/cli.py`: replaced the generic `_not_implemented` loop for `init`/`uninstall` with per-subcommand argparse setup; added `--project`/`--dry-run` flags; wired to `_run_init`/`_run_uninstall` handlers; added hidden `_hook` no-op subparser
- `tests/test_cli_smoke.py`: updated parametrize filter to exclude `init` and `uninstall`

### New Files

- `src/agentlog/hooks_install.py`: core install/uninstall logic
- `tests/test_hooks_install.py`: dedicated test coverage

### Key Changes

- **Sentinel-based identification**: agentlog-managed entries are identified by the `command` prefix `agentlog _hook` — survives JSON round-trips with no schema extensions and no Anthropic-owned field mutations
- **Two-layer design**: pure functions (`plan_install`, `plan_uninstall`, `diff`) operate on dicts with no filesystem access; I/O helpers (`load_settings`, `write_atomic`) and orchestrators (`run_init`, `run_uninstall`) sit on top
- **Atomic writes**: `write_atomic` writes to a sibling `.tmp` file in the same directory, then uses `os.replace` — a crash mid-write cannot corrupt `settings.json`
- **`PreToolUse` intentionally absent**: `EVENTS` tuple contains exactly the five v0.1 events; `PreToolUse` is deferred per CLAUDE.md hard rule #5
- **Idempotency**: `plan_install` scans for existing sentinel entries before appending; if already present, returns input unchanged; `run_init` detects `after == existing` and prints "already installed" without re-writing

### Architecture Impact

No hook handler hot-path code is in this feature. The `_hook` subparser is a no-op stub (exits 0 silently); real handler logic lands in ship-scope item #2. The `run_init`/`run_uninstall` orchestrators are only called by user-invoked CLI commands — never from hooks — so the <10ms hook latency budget (CLAUDE.md hard rule #1) is unaffected. The `hooks_install` module is pure stdlib; `pyproject.toml` dependencies remain empty.

## How to Use

### CLI Commands

```bash
# Register agentlog hooks in user-scope ~/.claude/settings.json
agentlog init

# Preview changes without writing (unified diff to stdout)
agentlog init --dry-run

# Register in project-scope ./.claude/settings.json instead
agentlog init --project

# Remove agentlog hooks; leaves all other hooks intact
agentlog uninstall

# Preview removal without writing
agentlog uninstall --dry-run

# Project-scope uninstall
agentlog uninstall --project
```

After `agentlog init`, Claude Code invokes `agentlog _hook <Event>` for the five registered events. This currently exits 0 silently; real handler logic (JSONL capture, SQLite indexing) arrives in ship-scope item #2 without requiring users to re-run `init`.

### Scope resolution

| Flag | Target file |
|---|---|
| *(default)* | `~/.claude/settings.json` |
| `--project` | `./.claude/settings.json` (cwd) |

## Configuration

No environment variables or config files required. Hook entries written to `settings.json` follow the Claude Code hook schema:

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "agentlog _hook SessionStart"}]}
    ],
    "PostToolUse": [
      {"matcher": "*", "hooks": [{"type": "command", "command": "agentlog _hook PostToolUse"}]}
    ]
  }
}
```

`PostToolUse` groups include `"matcher": "*"` per the DESIGN.md hook table. All other events use matcher-less groups.

## Testing

```bash
# Full test suite
pytest tests/ -q

# New module tests only
pytest tests/test_hooks_install.py -v

# Updated smoke tests
pytest tests/test_cli_smoke.py -v

# Type checking and linting
python -m mypy src tests
ruff check .
```

Test cases in `tests/test_hooks_install.py`:

| Category | Test | What it verifies |
|---|---|---|
| Unit | `test_plan_install_fresh_adds_all_five_events` | All five events added; `PreToolUse` absent |
| Unit | `test_plan_install_is_idempotent` | Second call returns identical dict |
| Unit | `test_plan_install_preserves_foreign_hooks` | Foreign entries survive |
| Unit | `test_plan_install_posttooluse_group_has_matcher` | `PostToolUse` group has `"matcher": "*"` |
| Unit | `test_plan_uninstall_removes_only_sentinel_entries` | Sentinel stripped; foreign survives |
| Unit | `test_plan_uninstall_empties_then_drops_keys` | `hooks` key removed when empty |
| Unit | `test_plan_uninstall_round_trips_original_settings` | Install then uninstall restores original |
| Unit | `test_plan_uninstall_preserves_pretooluse` | `PreToolUse` entries never touched |
| Unit | `test_plan_install_does_not_touch_pretooluse` | `PreToolUse` not in `EVENTS` |
| Unit | `test_diff_empty_when_no_change` | No-change diff returns `""` |
| Unit | `test_diff_has_unified_markers_on_change` | Changed diff has `---`/`+++` headers |
| FS | `test_load_settings_returns_empty_dict_when_missing` | Missing file → `{}` |
| FS | `test_load_settings_raises_on_malformed_json` | Bad JSON → `MalformedSettingsError` |
| FS | `test_write_atomic_creates_parent_dirs` | Parent dirs created automatically |
| FS | `test_write_atomic_no_tmp_file_left_behind` | `.tmp` file cleaned up on success |
| Integration | `test_run_init_dry_run_prints_diff_and_writes_nothing` | `--dry-run` prints diff, no file created |
| Integration | `test_run_init_fresh_creates_settings_file` | File created with five events |
| Integration | `test_run_init_is_idempotent_byte_for_byte` | Second run prints "already installed", file unchanged |
| Integration | `test_run_init_project_scope_writes_to_cwd` | `--project` writes to cwd, not HOME |
| Integration | `test_run_init_preserves_existing_user_hooks` | Foreign hooks survive init |
| Integration | `test_run_uninstall_removes_only_agentlog_entries` | Foreign hooks survive uninstall |
| Integration | `test_init_then_uninstall_yields_original` | Round-trip leaves file identical |
| Integration | `test_uninstall_with_no_settings_file_is_noop_success` | Missing file → rc=0, no creation |
| Integration | `test_malformed_settings_exits_nonzero_and_does_not_overwrite` | Bad JSON → rc≠0, file bytes unchanged |
| Integration | `test_uninstall_dry_run_prints_diff_and_writes_nothing` | `--dry-run` prints diff, file unchanged |
| Integration | `test_hook_noop_subparser_exits_zero` | `agentlog _hook SessionStart` exits 0 |

## Notes

- **No new dependencies.** Pure stdlib: `argparse`, `json`, `pathlib`, `sys`, `os`, `copy`, `difflib`. `pyproject.toml` dependencies remain `[]`.
- **Local-first.** No network calls at any point. Honors CLAUDE.md hard rule #6.
- **Concurrency.** `os.replace` provides last-writer-wins atomicity. True concurrent-write safety is a known limitation; not a v0.1 hardening target.
- **`_hook` subparser is hidden.** It does not appear in `--help` output (the `_choices_actions` list is patched). It is fully functional but undocumented until ship-scope item #2 replaces it with real handler logic.
- **Out of scope for this feature:** actual hook handler bodies (JSONL capture, cost computation, SQLite indexing), `~/.agentlog/` directory creation, `tail`/`ls`/`cost`/`view` subcommands, OTEL export.
