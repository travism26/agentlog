# Feature: `agentlog init` / `agentlog uninstall` — Claude Code hook registration

## Metadata

adw_id: `1b4319ab`
prompt: `/tmp/agentlog_init_prompt.md`

## Feature Description

Implement v0.1 ship-scope item #1 from `DESIGN.md`: the two CLI subcommands that manage agentlog's
Claude Code hook registrations in `settings.json`.

- `agentlog init` registers handler entries for the five v0.1 hook events
  (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd`) into either
  `~/.claude/settings.json` (default, user scope) or `./.claude/settings.json` (`--project` flag).
  `--dry-run` prints the exact diff that would be applied and writes nothing.
- `agentlog uninstall` symmetrically removes only agentlog-tagged entries, preserving every other
  hook that the user (or another tool) had configured.

Both commands are explicit, opt-in, idempotent, fail-clean, and stdlib-only. The hook *handlers*
themselves (the things `settings.json` points to) are out of scope for this task — handler
entries point to a placeholder command `agentlog _hook <Event>` that ship-scope item #2 will fill
in. A hidden no-op `_hook` subparser is added in this PR so a freshly installed `settings.json`
runs cleanly against the current build (handlers exit 0 silently — true fail-open per CLAUDE.md
hard rule #2).

## User Story

As a developer adopting agentlog to instrument my Claude Code sessions
I want one explicit command to wire (and another to un-wire) agentlog's hook handlers into Claude
Code's `settings.json`
So that I can opt into observability without hand-editing JSON and trust that uninstalling is
clean and non-destructive.

## Problem Statement

agentlog's value proposition depends on capturing every Claude Code session, which in turn
depends on agentlog's hook handlers being registered in `settings.json`. Hand-editing JSON is
error-prone, easy to forget, and a known adoption killer for observability tooling. Worse, a
botched installer that overwrites a user's existing hooks (from other tools, or from their own
customisations) is the single most trust-destroying failure mode for this project — once it
happens, no one re-installs.

There is no installer today: `src/agentlog/cli.py` registers `init`/`uninstall` as stubs that
return rc=2 with "not yet implemented".

## Solution Statement

Add a new stdlib-only module `src/agentlog/hooks_install.py` containing pure, well-tested
functions for the settings.json lifecycle:

1. Resolve the target path (user or project scope).
2. Load the existing settings (or `{}` if missing). Raise a typed `MalformedSettingsError` on
   `JSONDecodeError` so the CLI can exit non-zero with a clear message rather than silently
   overwrite.
3. Plan the new settings dict by merging agentlog's hook entries into the existing structure.
   Identify agentlog entries by a **sentinel prefix in the `command` string** (`agentlog _hook`)
   — the simplest strategy that survives JSON round-trips, requires no schema extensions, and
   doubles as the placeholder command from the prompt.
4. For uninstall, produce a plan that strips any entry whose `command` starts with the sentinel,
   then drops emptied event arrays and the top-level `hooks` key if it ends up empty.
5. Diff (`difflib.unified_diff` over pretty-printed JSON) when `--dry-run`.
6. Otherwise write atomically (temp-file in the same directory + `os.replace`).

Wire two new CLI handlers into `cli.py` that call `run_init` / `run_uninstall`, each accepting
`--project` and `--dry-run`. Update `tests/test_cli_smoke.py` so `init` and `uninstall` are no
longer in the "not yet implemented" parametrization, and add a dedicated `test_hooks_install.py`
covering dry-run, fresh install, idempotency, merge-preserving foreign hooks, malformed JSON,
project scope, and uninstall round-trip.

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` — the locked v0.1 design; "Hook integration" table and "Installation surface"
  snippet define the contract verbatim. `PreToolUse` is explicitly deferred.
- `CLAUDE.md` — hard rules; especially #2 (fail-open), #3 (never auto-install), #5 (no
  `PreToolUse`), #7 (schema-versioning / be conservative about Anthropic-owned fields),
  #8 (preserve existing hooks; idempotent).
- `ai_docs/research/1b4319ab-init-uninstall-cli-analysis.md` — pre-planning research; the
  "Recommendations" section ships the module surface and test names this plan adopts.
- `src/agentlog/cli.py` — current scaffold. The `for name in SUBCOMMANDS: ... set_defaults(func=_not_implemented)` loop must be replaced with per-subcommand setup that
  registers `--project` and `--dry-run` on `init`/`uninstall` and wires them to real handlers.
- `src/agentlog/__init__.py` — exports `__version__`; not modified.
- `src/agentlog/__main__.py` — `python -m agentlog` shim; not modified.
- `tests/test_cli_smoke.py` — parametrizes over `SUBCOMMANDS`; must exclude `init`/`uninstall`
  from the "not yet implemented" assertion once they ship real behaviour.
- `pyproject.toml` — already stdlib-only; no dep changes. mypy strict + ruff already configured.

### New Files

- `src/agentlog/hooks_install.py` — core install/uninstall logic (pure functions + orchestrators).
- `tests/test_hooks_install.py` — dedicated coverage for the new module and CLI handlers.

## Implementation Plan

### Phase 1: Foundation

Establish the constants and pure helpers in the new module before any I/O:

- The `EVENTS` tuple (the five v0.1 hook event names; explicit comment that `PreToolUse` is
  deferred per CLAUDE.md hard rule #5).
- The `HOOK_COMMAND_PREFIX = "agentlog _hook"` sentinel and a builder
  `agentlog_command(event: str) -> str` that returns `f"agentlog _hook {event}"`.
- A typed exception `MalformedSettingsError(Exception)`.
- Pure functions: `plan_install(existing: dict[str, Any]) -> dict[str, Any]`,
  `plan_uninstall(existing: dict[str, Any]) -> dict[str, Any]`,
  `diff(before: dict[str, Any], after: dict[str, Any]) -> str`.
- Pure functions are unit-testable without touching the filesystem and form the safety net for
  the I/O layer above.

### Phase 2: Core Implementation

Add the I/O and CLI surface on top of the pure layer:

- `resolve_settings_path(project: bool) -> Path` — returns `Path.home() / ".claude" / "settings.json"`
  when `project` is False, else `Path.cwd() / ".claude" / "settings.json"`.
- `load_settings(path: Path) -> dict[str, Any]` — returns `{}` when the file does not exist;
  raises `MalformedSettingsError` (wrapping the original `JSONDecodeError`) when JSON is
  invalid. Never overwrites on error.
- `write_atomic(path: Path, data: dict[str, Any]) -> None` — `mkdir(parents=True, exist_ok=True)`
  on the parent, write to a sibling `*.tmp` in the same directory, then `os.replace`.
- `run_init(*, project: bool, dry_run: bool) -> int` and
  `run_uninstall(*, project: bool, dry_run: bool) -> int` — orchestrate load → plan → diff →
  write (or just print on dry-run). Return 0 on success, non-zero with a clear stderr message
  on malformed input / permission errors.
- In `cli.py`: replace the loop's single `_not_implemented` wiring for `init` and `uninstall`
  with per-subcommand argparse setup (add `--project` and `--dry-run` flags), and point `func=`
  at the new handlers. Keep `SUBCOMMANDS` intact so existing tests still discover all six names.
- In `cli.py`: add a hidden `_hook` subparser (not in `SUBCOMMANDS`, omitted from help via
  `add_parser("_hook", help=argparse.SUPPRESS)`) that takes a positional `event` and returns
  0 unconditionally. This makes the freshly installed `settings.json` runnable today without
  127-erroring during the demo; the real handler logic lands in ship-scope item #2.

### Phase 3: Integration

- Update `tests/test_cli_smoke.py::test_subcommands_registered_but_not_implemented` to
  parametrize over `[c for c in SUBCOMMANDS if c not in {"init", "uninstall"}]`, keeping
  smoke coverage for the four remaining stubs (`tail`, `ls`, `cost`, `view`).
- Add `tests/test_hooks_install.py` with the cases enumerated in the Testing Strategy section.
- Verify via `agentlog init --dry-run` end-to-end (CLI → handler → module → diff to stdout, no
  filesystem writes), and `agentlog uninstall --dry-run` against a seeded `settings.json` under
  a `tmp_path` cwd with `--project`.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Create the hooks_install module skeleton with constants and exception

- Create `src/agentlog/hooks_install.py` with `from __future__ import annotations`.
- Define `EVENTS: tuple[str, ...] = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")`.
  Add a single-line comment: `# PreToolUse intentionally omitted — deferred past v0.1 (CLAUDE.md rule #5).`
- Define `HOOK_COMMAND_PREFIX = "agentlog _hook"`.
- Define `def agentlog_command(event: str) -> str: return f"{HOOK_COMMAND_PREFIX} {event}"`.
- Define `class MalformedSettingsError(Exception): ...` with one attribute: the offending path.

### 2. Implement the pure plan/diff functions

- `plan_install(existing: dict[str, Any]) -> dict[str, Any]`:
  - Deep-copy `existing` (use `copy.deepcopy`).
  - Ensure `result.setdefault("hooks", {})` and `result["hooks"].setdefault(event, [])` for each
    event in `EVENTS`.
  - For each event, scan the existing list for any entry whose nested `hooks[].command` starts
    with `HOOK_COMMAND_PREFIX`. If none, append a new group:
    `{"hooks": [{"type": "command", "command": agentlog_command(event)}]}` — and for `PostToolUse`
    include `"matcher": "*"` on the group (matches DESIGN.md hook table).
  - If an agentlog-tagged entry already exists for the event, leave the dict alone (idempotent;
    respects any user edits to the command path).
  - Return the new dict.
- `plan_uninstall(existing: dict[str, Any]) -> dict[str, Any]`:
  - Deep-copy.
  - For each event under `result.get("hooks", {})`, filter out any group whose `hooks[]` list
    consists entirely of agentlog entries (command starts with `HOOK_COMMAND_PREFIX`). For mixed
    groups (foreign + agentlog), drop only the agentlog `hooks[]` entries within the group;
    if that leaves the group's `hooks` list empty, drop the group too.
  - After filtering, drop event keys with empty lists; drop the top-level `hooks` key if it
    ends up `{}`.
  - Return the new dict.
- `diff(before: dict[str, Any], after: dict[str, Any]) -> str`:
  - `json.dumps(..., indent=2, sort_keys=True).splitlines(keepends=True)` on both sides.
  - Use `difflib.unified_diff(..., fromfile="settings.json (current)", tofile="settings.json (after)", n=3)`.
  - Return the joined string.

### 3. Implement filesystem helpers

- `resolve_settings_path(project: bool) -> Path` as above.
- `load_settings(path: Path) -> dict[str, Any]`:
  - If `not path.exists()`: return `{}`.
  - Open and `json.load`. On `json.JSONDecodeError as exc`: raise
    `MalformedSettingsError(path)` chained with `raise ... from exc`.
- `write_atomic(path: Path, data: dict[str, Any]) -> None`:
  - `path.parent.mkdir(parents=True, exist_ok=True)`.
  - Write to `path.with_suffix(path.suffix + ".tmp")` (or a `tempfile.NamedTemporaryFile` in the
    same directory — important: same directory so `os.replace` is atomic).
  - `json.dump(data, fp, indent=2, sort_keys=True)` followed by a trailing newline.
  - `os.replace(tmp_path, path)`.

### 4. Implement the run_init / run_uninstall orchestrators

- `run_init(*, project: bool, dry_run: bool) -> int`:
  - Resolve path.
  - Try `existing = load_settings(path)`. On `MalformedSettingsError`: print
    `f"error: {path} contains invalid JSON; refusing to overwrite. Backup the file and re-run."`
    to stderr; return 1.
  - `after = plan_install(existing)`.
  - If `after == existing`: print `f"agentlog hooks already installed at {path}"` to stdout;
    return 0 (idempotency: no-op success).
  - If `dry_run`: print the diff to stdout (or `"(no changes)"` if empty); return 0.
  - Else: `write_atomic(path, after)`; print `f"installed agentlog hooks to {path}"`; return 0.
- `run_uninstall(*, project: bool, dry_run: bool) -> int`:
  - Resolve path.
  - If file does not exist: print `f"no settings.json at {path}; nothing to uninstall"`;
    return 0.
  - Try `existing = load_settings(path)`. Malformed → same clean error as above; return 1.
  - `after = plan_uninstall(existing)`.
  - If `after == existing`: print `f"no agentlog hooks found in {path}"`; return 0.
  - If `dry_run`: print diff; return 0.
  - Else: `write_atomic(path, after)`; print `f"uninstalled agentlog hooks from {path}"`;
    return 0.

### 5. Wire the new handlers into cli.py

- Refactor `build_parser` so the `SUBCOMMANDS` loop only registers `_not_implemented` for
  subcommands that are *still* stubs (i.e., `tail`, `ls`, `cost`, `view`). Register `init` and
  `uninstall` explicitly above/below the loop with their own subparser setup:
  - `sp.add_argument("--project", action="store_true", help="Write to ./.claude/settings.json (project scope) instead of ~/.claude/settings.json")`
  - `sp.add_argument("--dry-run", action="store_true", help="Print what would change; write nothing")`
  - `sp.set_defaults(func=_run_init)` / `_run_uninstall`
- Define `_run_init(args)` and `_run_uninstall(args)` that call into
  `hooks_install.run_init(project=args.project, dry_run=args.dry_run)` etc.
- Keep `SUBCOMMANDS` intact (still all six names) so `test_no_args_prints_help_and_returns_zero`
  continues to assert all six appear in help output.

### 6. Add the hidden `_hook` no-op subparser

- In `build_parser`, after registering the documented subcommands, add a hidden subparser:
  `sp = sub.add_parser("_hook", help=argparse.SUPPRESS)`
  `sp.add_argument("event")`
  `sp.set_defaults(func=lambda args: 0)`
- This means a freshly installed `settings.json` runs cleanly against this build — Claude Code
  invokes `agentlog _hook SessionStart`, which exits 0 silently. Document with a one-line
  comment that the real handler lands in ship-scope item #2.

### 7. Update tests/test_cli_smoke.py

- Change the parametrize line to
  `@pytest.mark.parametrize("cmd", [c for c in SUBCOMMANDS if c not in {"init", "uninstall"}])`.
- Leave `test_no_args_prints_help_and_returns_zero` and `test_version_flag` alone.

### 8. Add tests/test_hooks_install.py

- Use `tmp_path` and `monkeypatch.setenv("HOME", str(tmp_path))` (or monkeypatch `Path.home`)
  for user-scope tests so no real `~/.claude/` is touched. Use `monkeypatch.chdir(tmp_path)`
  for project-scope tests.
- Cover the cases enumerated in Testing Strategy / Edge Cases.

### 9. Run compile checks and tests

- Run the compile-check commands in the "Compile Checks" section.
- Run `.venv/bin/pytest tests/ -q` (full suite; both updated smoke tests and new install tests).
- Run `.venv/bin/python -m mypy src tests` (strict mode is configured) and `.venv/bin/ruff check .`.

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation:

- Look for files like `HOW_TO_CREATE_TESTS.md`, `TESTING.md`, or `README.md` in the relevant test
  directory. (As of 2026-05-27, none exist in `tests/`.)
- Follow existing patterns from `tests/test_cli_smoke.py`: `from __future__ import annotations`,
  pytest `capsys` for capturing stdout/stderr, `tmp_path` for filesystem isolation,
  `monkeypatch` for HOME/cwd, and `parametrize` for table-driven cases.
- Never touch real `~/.claude/`. Always use `tmp_path`.

### Unit Tests (in `tests/test_hooks_install.py`)

- `test_plan_install_fresh_adds_all_five_events` — pure call with `{}`; assert the five event
  keys exist under `result["hooks"]`, each with one group whose command starts with the
  sentinel. Assert `PreToolUse` is NOT in the result.
- `test_plan_install_is_idempotent` — apply `plan_install` twice; second call returns identical
  dict to the first.
- `test_plan_install_preserves_foreign_hooks` — pre-seed `{"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "other-tool"}]}]}}`;
  assert the foreign entry is still present after planning.
- `test_plan_uninstall_removes_only_sentinel_entries` — pre-seed mixed (foreign + agentlog);
  assert foreign survives, agentlog stripped.
- `test_plan_uninstall_empties_then_drops_keys` — install then uninstall; the `hooks` key is
  dropped entirely.
- `test_plan_uninstall_round_trips_original_settings` — start with a foreign-only settings dict;
  install then uninstall; assert the result equals the original.
- `test_agentlog_command_format` — `agentlog_command("SessionStart") == "agentlog _hook SessionStart"`.
- `test_diff_is_unified_format_or_empty` — `diff(x, x) == ""`; `diff({}, plan_install({}))`
  contains `+++` and `---` headers.

### Integration Tests (in `tests/test_hooks_install.py`)

- `test_run_init_dry_run_prints_diff_and_writes_nothing` — monkeypatch HOME, call CLI
  `main(["init", "--dry-run"])`, assert rc==0, stdout contains diff markers, target file does
  not exist on disk.
- `test_run_init_fresh_creates_settings_file` — monkeypatch HOME to `tmp_path`, call
  `main(["init"])`, assert rc==0, file exists at `tmp_path / ".claude" / "settings.json"`,
  loaded JSON has the five events with sentinel commands.
- `test_run_init_is_idempotent_byte_for_byte` — call `main(["init"])` twice; second call prints
  "already installed" and the file's bytes are unchanged.
- `test_run_init_project_scope_writes_to_cwd` — monkeypatch cwd to `tmp_path`; call
  `main(["init", "--project"])`; assert `tmp_path / ".claude" / "settings.json"` exists, not
  the HOME path.
- `test_run_init_preserves_existing_user_hooks` — pre-seed `tmp_path / ".claude" / "settings.json"`
  with a foreign hook; run init; assert the foreign hook is still in the file.
- `test_run_uninstall_removes_only_agentlog_entries` — pre-seed a mixed settings.json;
  run uninstall; assert foreign survives, agentlog gone.
- `test_init_then_uninstall_yields_original` — pre-seed foreign settings; init; uninstall;
  assert resulting file content equals the pre-seeded content (modulo JSON formatting; compare
  parsed dicts).
- `test_uninstall_with_no_settings_file_is_noop_success` — no file present; rc==0, message
  printed, no file created.
- `test_malformed_settings_exits_nonzero_and_does_not_overwrite` — write garbage to the target
  file; run init; assert rc!=0, stderr contains the path and "invalid JSON", file bytes
  unchanged.

### Edge Cases

- Malformed JSON in existing `settings.json` — clean non-zero exit, no overwrite, original
  bytes preserved (verify by reading before/after).
- Concurrent sessions: not a v0.1 hardening target. Atomic `os.replace` provides last-writer-wins
  safety, which is acceptable; document as a known limitation.
- `--project` when `./.claude/` does not exist — parent dirs created via
  `mkdir(parents=True, exist_ok=True)`.
- Pre-seeded settings.json containing an event group with `matcher` set (e.g., user has a
  `PostToolUse` matcher of `Edit` for another tool) — agentlog adds its own group with
  `matcher: "*"`; both coexist (separate groups in the list).
- Pre-seeded settings.json containing an existing entry whose command literally matches our
  sentinel but whose surrounding group contains additional foreign entries — `plan_uninstall`
  must drop only that single entry's element from `group["hooks"]`, not the entire group.
- `PreToolUse` key present in user's existing settings.json (added by them or another tool) —
  agentlog must never touch it; verify with a test.

## Acceptance Criteria

- `agentlog init --dry-run` exits 0 and prints a unified diff to stdout; the target file is
  unchanged (or absent) on disk afterwards.
- `agentlog init` is idempotent: running twice in a row leaves `settings.json` byte-identical
  after the second run, and the second run prints an "already installed" message rather than
  re-writing.
- `agentlog init` registers exactly the five v0.1 events; `PreToolUse` is never added.
- `agentlog uninstall` removes only entries whose `command` starts with `agentlog _hook`;
  hooks added by other tools survive untouched.
- `agentlog init --project` writes to `./.claude/settings.json` in cwd, creating `./.claude/`
  if missing.
- `agentlog uninstall` against a settings.json that has no agentlog entries is a clean
  success (rc=0, informative stdout message), with the file untouched.
- Malformed JSON in the target file causes a non-zero exit with a clear stderr message
  naming the path; the file is never overwritten.
- `init`/`uninstall` exit 0 on success; non-zero with a clear message on failure.
- Test suite passes: existing `tests/test_cli_smoke.py` (with the parametrize update) plus the
  new `tests/test_hooks_install.py`, all cases above passing under `pytest`.
- No new runtime dependencies — `pyproject.toml`'s `dependencies = []` is unchanged.
- mypy strict and ruff pass.

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These run during the
build phase — do NOT include pytest, linters, or pipeline runs (those belong to dedicated CI
phases).

- `.venv/bin/python -m py_compile src/agentlog/cli.py src/agentlog/hooks_install.py && echo "OK"` — Verify no syntax errors in the two source files.
- `.venv/bin/python -c "from agentlog import cli, hooks_install; print('import OK')"` — Verify both modules import cleanly.
- `.venv/bin/agentlog --help` — Verify the CLI still wires up (and that the documented subcommands all appear).
- `.venv/bin/agentlog init --help` — Verify `--project` and `--dry-run` flags are registered.
- `.venv/bin/agentlog uninstall --help` — Same for uninstall.

## Notes

- **No new libraries**. Stdlib only: `argparse`, `json`, `pathlib`, `sys`, `os`, `copy`,
  `difflib`, optionally `tempfile`. Honors CLAUDE.md / DESIGN.md "stdlib-only core".
- **Sentinel strategy**. Each agentlog-managed hook entry's `command` starts with the literal
  `agentlog _hook`. Uninstall identifies "our" entries by that prefix. The sentinel was chosen
  over a side-file inventory (drift risk) or a custom JSON key (Anthropic-owned schema risk).
- **`PreToolUse` is intentionally NOT in `EVENTS`**. Hard rule #5. A comment in `hooks_install.py`
  records this for future contributors.
- **Hidden `_hook` no-op subparser**. Added so a freshly installed `settings.json` runs cleanly
  against this build — Claude Code invokes `agentlog _hook <Event>`, which currently exits 0
  silently. The real handler logic lands in ship-scope item #2 and will replace this stub
  without changing the user-facing `settings.json` (so users don't need to re-run `init`).
- **Atomicity**. Writes go through a sibling temp file + `os.replace` so a crash mid-write
  cannot leave a corrupted `settings.json`. Concurrent multi-process safety is best-effort
  (last writer wins); not a v0.1 hardening target.
- **Privacy / local-first**. Pure filesystem operation; no network calls. Honors hard rule #6.
- **Out of scope for this task** (deferred to ship-scope item #2 or later): actual hook handler
  bodies (writing JSONL events, computing costs, indexing in SQLite); `~/.agentlog/` directory
  creation (handler runtime owns that); `tail`, `ls`, `cost`, `view` subcommands; OTEL export.
