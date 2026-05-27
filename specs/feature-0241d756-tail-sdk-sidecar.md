# Feature: `agentlog tail` — SDK sidecar mode

## Metadata

adw_id: `0241d756`
prompt: `/tmp/agentlog_step3_prompt.md`

## Feature Description

Implement v0.1 ship-scope item #3 from `DESIGN.md`: **`agentlog tail <path>`** — the scripted-mode
twin of `agentlog _hook` capture. Where ship-scope item #2 (commit `0d08895`) captured *interactive*
Claude Code sessions via hook handlers reading from stdin, this feature captures *scripted* Claude
Code SDK / Anthropic SDK runs by ingesting their `cc_raw_output.jsonl` artifacts from disk into
the same unified `runs/<id>/{state.json, events.jsonl, cost.json}` schema.

This proves the central thesis from `DESIGN.md` lines 87-131: "Both data sources produce the same
directory structure." After this feature lands, the three remaining v0.1 read-time commands
(`ls`, `cost`, `view`) can treat hooks-mode and sdk-mode runs uniformly — they read one schema,
discriminated only by the `source: "hooks" | "sdk"` field.

The work lives in one new module (`src/agentlog/tail.py`), ~30 lines of `cli.py` wiring, a two-line
addition to `_constants.py`, a new test suite (`tests/test_tail.py`), and a one-line edit to
`tests/test_cli_smoke.py`. **No new runtime dependencies** — `pyproject.toml dependencies = []`
stays empty. **No modification to `capture.py`** — the hooks-mode capture path is untouched.

The failure contract is **inverted** from hooks mode: hook handlers are fail-open (CLAUDE.md hard
rule #2) because they run in Claude Code's hot path; `tail` is fail-loud because it is a
user-invoked CLI command and silent failures would erode trust in the read-time tooling that
depends on its output.

## User Story

As a developer driving Claude Code from my own Python scripts (or the Anthropic SDK directly)
I want to ingest the `cc_raw_output.jsonl` files my orchestrator already writes to disk
So that those scripted runs show up alongside my interactive `claude` sessions in
`agentlog ls / cost / view`, without changing how my orchestrator works.

## Problem Statement

Many agentlog users — including the agentlog repo itself — drive Claude Code via subprocess from
Python automation (`claude -p "<prompt>" --output-format stream-json --verbose`) and end up with
one or more `cc_raw_output.jsonl` files per run. The agentlog repo alone already has five ADW
runs × ~8 phases each of these files in `agents/<adw_id>/<phase>/cc_raw_output.jsonl`.

Without `tail`, scripted-mode data is invisible to agentlog. Two specific gaps:

1. Users who orchestrate Claude Code via SDK cannot use `agentlog ls / cost / view` to inspect
   what their orchestrator produced — they're stuck cat-ing raw JSONL.
2. The unified-schema thesis in `DESIGN.md` is unproven: a `runs/<id>/` directory only ever holds
   hooks-mode output today, so the claim that downstream readers can be source-agnostic is
   asserted, not demonstrated.

`tail` closes both gaps with one module.

## Solution Statement

Add `src/agentlog/tail.py` exporting one public function:

```python
def run_tail(
    path: Path,
    *,
    run_id: str | None,
    source_name: str | None,
    dry_run: bool,
    force: bool,
) -> int
```

It walks `<path>` (file or directory) for `cc_raw_output.jsonl` files, derives a deterministic
`sdk-<session_id>` run id from each file's `system/init` record (falling back to
`sdk-<sha1(abspath)[:12]>` for truncated files), translates each stream-json record into the
hooks-mode-compatible `runs/<id>/{state.json, events.jsonl, cost.json}` layout, and returns the
appropriate exit code (`0` happy, `2` for user errors like missing path or invalid flag
combinations, `1` for runtime IO failures).

Key design choices, all locked by the prompt:

- **Stream-line iteration**, never `f.read()`. Large `cc_raw_output.jsonl` files (>10MB) must not
  load into memory.
- **Idempotent by default**: if `runs/<run_id>/events.jsonl` already exists, print
  `"already ingested {path} → {run_dir}; use --force to re-ingest"` and skip with rc=0.
- **`--force` unlinks the three known files** (`state.json`, `events.jsonl`, `cost.json`) — not
  `rmtree(run_dir)`. Future features may add sibling files to the run dir that `tail` shouldn't
  wipe.
- **`--dry-run` short-circuits all writes** but parses the file to count records and report what
  *would* be written. Honors idempotency: if a re-ingest would be a no-op, `--dry-run` reports
  the "already ingested" message rather than the "would write" output.
- **User-supplied `--run-id` is stored verbatim** (no auto-`sdk-` prefix). The `sdk-` prefix is
  reserved for *auto-derived* ids. Documented in the CLI help so users can namespace their own
  ids if they want.
- **Helpers are duplicated, not exposed.** `capture.py`'s `_append_event`, `_write_state`,
  `_write_cost`, `_truncate`, `_isoformat`, `_log_self`, `_data_root`, `_session_dir` stay
  private. `tail.py` carries its own copies. Rationale: the two modules have inverted failure
  contracts (fail-open vs fail-loud) that will drift over time; a shared helper module would
  force premature abstraction. The total duplicated surface is ~40 lines.

`cli.py` drops `"tail"` from `_STUB_SUBCOMMANDS`, adds a real subparser branch in the existing
`for name in SUBCOMMANDS:` loop with four arguments (`--run-id`, `--source-name`, `--dry-run`,
`--force`), and adds an `_run_tail` handler that calls `tail.run_tail(...)`.

`_constants.py` gains `SOURCE_SDK: str = "sdk"` (mirrors `SOURCE_HOOKS`).

## Relevant Files

Use these files to implement the feature:

- `DESIGN.md` (lines 87-131, 192) — unified-schema thesis and ship-scope row that authorizes this work.
- `CLAUDE.md` — hard rules; in particular #6 (local-first, no network), #7 (`schema_version: 1`
  on every record, unknown payloads logged not dropped), and the explicit fail-loud contract for
  user-invoked CLI commands (inverse of hard rule #2).
- `ai_docs/research/0241d756-tail-sdk-sidecar-analysis.md` — research pass for this feature.
  Documents stream-json record shapes, run-id derivation, helper re-use vs duplicate analysis,
  and edge cases that drove the spec.
- `src/agentlog/cli.py` (lines 11-13, 24-55, 74-76) — entry point. `tail` currently registered as
  a stub via `_STUB_SUBCOMMANDS`. Must drop the stub registration and add a real subparser.
- `src/agentlog/_constants.py` (lines 28-34) — add `SOURCE_SDK = "sdk"`.
- `src/agentlog/capture.py` (lines 38-122) — pattern reference for writer helpers. **Do not
  modify.** `tail.py` carries its own copies of `_append_event` / `_write_state` / `_write_cost`
  / `_isoformat` / `_truncate` / `_log_self`.
- `agents/1b4319ab/researcher/cc_raw_output.jsonl` — real in-tree fixture named in the
  acceptance criteria for smoke testing.
- `agents/<other adw runs>/<phase>/cc_raw_output.jsonl` — additional real fixtures for the
  directory-walk acceptance criterion.
- `tests/test_capture.py` — pattern reference for `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))`.
- `tests/test_cli_smoke.py` (line 25) — add `"tail"` to the excluded-from-stub set.
- `pyproject.toml` (lines 28-29) — `dependencies = []` must stay empty; mypy strict and ruff
  (E/W/F/I/B/UP/SIM) are gating.
- `.adw/adw_modules/agent.py` (lines 262-341) — *reference only*, do NOT import. Contains
  `parse_jsonl_output` / `convert_jsonl_to_json` / `save_last_entry_as_raw_result` that show the
  stream-json record shape. The runtime must NOT depend on `.adw/`.

### New Files

- `src/agentlog/tail.py` — translator + writer + run-id derivation + idempotency check. Public
  surface: `run_tail()`. ~200-250 LOC including helper duplicates.
- `tests/test_tail.py` — covers happy path, idempotency, `--force`, `--dry-run`, missing file,
  empty file, directory walk, multiple-files-with-`--run-id` error, unknown record type →
  `event: "unknown"`, truncated mid-stream. ~250 LOC.
- `tests/fixtures/sdk_minimal.jsonl` *(optional)* — hand-written micro stream-json (10-20 lines:
  one `system/init`, one `assistant` with text + tool_use, one `user` with tool_result, one real
  user prompt, one `result/success`). Stable, tiny, easy to assert against. Alternative: use
  the in-tree `agents/<adw_id>/<phase>/cc_raw_output.jsonl` directly — but those files are
  candidates for cleanup, so a checked-in fixture is safer.

## Implementation Plan

### Phase 1: Foundation

Add the constant and confirm the scaffold edits land before `tail.py` exists:

1. Add `SOURCE_SDK: str = "sdk"` to `src/agentlog/_constants.py`.
2. Drop `"tail"` from `_STUB_SUBCOMMANDS` in `src/agentlog/cli.py` and add an empty `elif name == "tail":` branch wired to a placeholder `_run_tail` that returns `2`. This isolates the CLI-wiring diff from the translator-logic diff.
3. Update `tests/test_cli_smoke.py:25` to exclude `"tail"` from the stub-check parametrize set.
4. Confirm: `pytest tests/test_cli_smoke.py` still passes (the new branch returns 2, which currently looks like the stub; the assertion in step 3 keeps the test green).

### Phase 2: Core Implementation

Build `src/agentlog/tail.py` bottom-up:

1. **Module docstring** — cite CLAUDE.md hard rules pinned by this module (#6 local-first, #7 schema-versioned), and explicitly note the fail-loud contract (inverse of #2).
2. **Helpers** (duplicated from `capture.py`, sanitized for fail-loud semantics): `_data_root`, `_session_dir`, `_isoformat`, `_truncate`, `_log_self`, `_append_event`, `_write_state`, `_write_cost`. `_log_self` retains its swallow-on-error behavior (best-effort logging is fine in both contracts); main-path I/O errors are NOT swallowed.
3. **Run-id derivation**:
   ```python
   def _derive_run_id(path: Path, explicit: str | None, root: Path) -> tuple[str, bool]:
       """Return (run_id, used_fallback). explicit → verbatim; init present → 'sdk-' + session_id; else 'sdk-' + sha1(abspath)[:12]."""
   ```
   Reads only the first non-blank line of the file to locate the `system/init` record. Returns
   `(run_id, used_fallback)`. Tests can drive this without writing any files.
4. **Translator** — pure function, no I/O, no clock:
   ```python
   def _translate(
       records: Iterable[dict[str, Any]],
       *,
       run_id: str,
       abs_path: str,
       now: datetime,
   ) -> Iterator[dict[str, Any]]:
   ```
   Yields event-dict records ready for `_append_event`. Caller injects `now()` and `abs_path` so
   tests are deterministic. Translator also returns (via a tuple or by yielding a final
   sentinel; recommend a separate `_summarize` pass) the derived `state.json` + `cost.json`
   contents — or the caller can fold them as it consumes the iterator. Pick whichever yields the
   simplest test seams; recommend the caller-folds approach.
5. **Per-record translation rules** (locked by the prompt + research):

   | Record `type` | Subtype | Translate to |
   |---|---|---|
   | `system` | `init` | `session_start` event; seed `state.json` with `session_id`, `cwd`, `model`, `started_at` (file-mtime of first line OR `now()` — use mtime if available, else now) |
   | `assistant` | (text block) | `assistant_text` event with `text`, `text_bytes`, `truncated_bytes` (mirror `_truncate` to `MAX_INLINE_BYTES`) |
   | `assistant` | (tool_use block) | `tool_use` event with `tool`, `params_summary`, `result_summary: null`, `duration_ms: null`. Back-fill of result is deferred (v0.2+). |
   | `assistant` | (thinking block) | **Skip** in v0.1 (stay minimal) |
   | `user` | (real prompt) | `prompt` event mirroring hooks-mode shape |
   | `user` | (tool_result only) | **Skip** — back-fill is deferred |
   | `result` | `success` / `error_*` | `stop` event with `usage`; update `cost.json` totals; on `is_error: true` flag `state.session_failed = true` |
   | `system` | `api_retry` | `event: "unknown"` row with the raw record (mirror hooks-mode `_on_unknown`) |
   | `rate_limit_event` | — | `event: "unknown"` row |
   | anything else | — | `event: "unknown"` row |

   Every emitted event carries: `schema_version: 1`, `source: "sdk"`, `session_id: <run_id>`,
   `timestamp: <iso>`, and `sdk_source_file: <abs_path>` (the latter is the SDK-mode tracer that
   lets a reader walk back to the raw JSONL).

6. **Idempotency check**:
   ```python
   def _is_already_ingested(run_dir: Path) -> bool:
       return (run_dir / "events.jsonl").exists()
   ```
   Called before any writes. On `--force`, unlink `state.json`, `events.jsonl`, `cost.json`
   (`Path.unlink(missing_ok=True)` for each).
7. **Directory walk**: `Path.rglob("cc_raw_output.jsonl")` with a depth cap of 5 (compute depth
   via `parts` length relative to the walk root; skip anything deeper). Sort results for
   deterministic test output.
8. **Public `run_tail(...)`** — top-level orchestrator:
   ```
   1. If path doesn't exist → print stderr, return 2.
   2. If path is a directory:
        - find files (sorted).
        - If empty → print "no cc_raw_output.jsonl files found under <dir>", return 0.
        - If run_id is set and N > 1 → print error, return 2.
        - For each file: _process_one(file, ...), accumulate worst rc.
   3. If path is a file: _process_one(file, ...).
   4. Return rc.
   ```
9. **Per-file `_process_one(file, ...)`**:
   ```
   1. Derive run_id.
   2. Compute run_dir = _session_dir(_data_root(), run_id).
   3. If _is_already_ingested(run_dir):
        - If dry_run: print "would skip (already ingested)"; return 0.
        - If force: unlink three files; continue.
        - Else: print "already ingested ..."; return 0.
   4. Open file, iter lines, json.loads each, skipping blank lines.
        - On JSONDecodeError: emit `event: "unknown"` with `raw: "<unparseable line N>"` and continue.
   5. If dry_run: count records by translated event kind, print summary, return 0.
   6. Write events.jsonl line by line; build state + cost dicts as we go.
   7. After loop: _write_state(run_dir, state); _write_cost(run_dir, cost).
   8. Print one-line per-file summary: "<file> → <run_dir> (N events)".
   9. Return 0.
   ```

### Phase 3: Integration

1. Replace the `_run_tail` placeholder in `cli.py` with the real handler:
   ```python
   def _run_tail(args: argparse.Namespace) -> int:
       return tail.run_tail(
           Path(args.path),
           run_id=args.run_id,
           source_name=args.source_name,
           dry_run=args.dry_run,
           force=args.force,
       )
   ```
2. Wire `from agentlog import tail` at the top of `cli.py` alongside the existing
   `from agentlog import __version__, capture, hooks_install`.
3. Verify the `tail` subparser appears in `agentlog --help` output (no longer marked "not yet
   implemented").
4. Manual smoke against `agents/1b4319ab/researcher/cc_raw_output.jsonl` per the acceptance
   criteria.

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

### 1. Add `SOURCE_SDK` constant

- Edit `src/agentlog/_constants.py`: add `SOURCE_SDK: str = "sdk"` next to `SOURCE_HOOKS`.
- No other changes — `EVENTS` is hook-specific and must NOT grow `"assistant_text"`.

### 2. Drop `tail` from CLI stubs (scaffold only)

- Edit `src/agentlog/cli.py`: remove `"tail"` from `_STUB_SUBCOMMANDS`.
- Add an `elif name == "tail":` branch inside the `for name in SUBCOMMANDS:` loop with the four
  argparse flags below. Wire `set_defaults(func=_run_tail)`.
- Add a placeholder `_run_tail` that returns `2` and an empty stderr message — to be replaced in
  step 9. This keeps the CLI compiling without `tail.py` existing yet.
- Argparse flags (exact text matters for `--help` stability):
  - positional `path` — `help="file or directory containing cc_raw_output.jsonl"`
  - `--run-id` — `help="explicit run id (only valid for single-file ingestion)"`
  - `--source-name` — `help="human label written into state.json (default: basename of <path>)"`
  - `--dry-run` — `action="store_true"`, `help="parse and report; write nothing"`
  - `--force` — `action="store_true"`, `help="re-ingest even if events already exist"`

### 3. Update `test_cli_smoke.py`

- Change line 25 from `if c not in {"init", "uninstall"}` to `if c not in {"init", "uninstall", "tail"}`.
- Run `pytest tests/test_cli_smoke.py -q` and confirm all tests pass.

### 4. Add `tests/fixtures/sdk_minimal.jsonl`

- Hand-write a 10-20 line minimal stream-json file covering:
  - 1 × `{"type":"system","subtype":"init","session_id":"abc-123","cwd":"/tmp","model":"claude-opus-4-7","tools":["Read","Edit"]}`
  - 1 × `{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/tmp/x"}}]},"session_id":"abc-123"}`
  - 1 × `{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"file contents"}]},"session_id":"abc-123"}`
  - 1 × `{"type":"user","message":{"content":[{"type":"text","text":"please do it"}]},"session_id":"abc-123"}`
  - 1 × `{"type":"system","subtype":"api_retry","attempt":1,"session_id":"abc-123"}` (locks the unknown-record path)
  - 1 × `{"type":"result","subtype":"success","usage":{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":5,"cache_creation_input_tokens":1},"duration_ms":1234,"total_cost_usd":0.001,"is_error":false,"session_id":"abc-123"}`

### 5. Create `src/agentlog/tail.py` — helpers

- `from __future__ import annotations` at top.
- Module docstring citing CLAUDE.md rules pinned (#6 local-first, #7 schema-versioned, fail-loud).
- Import block: `argparse` (NO — only needed in cli.py), `hashlib`, `json`, `os`, `sys`, `pathlib.Path`, `datetime`, `typing.Any`.
- Duplicate the eight helpers from `capture.py`: `_data_root`, `_session_dir`, `_isoformat`,
  `_truncate`, `_log_self`, `_append_event`, `_write_state`, `_write_cost`. Function signatures
  identical to capture.py's so a future `_io.py` extraction is mechanical.

### 6. Add `_derive_run_id`

- Reads only the first non-blank line of the file. JSON-parses it; if it's a dict with
  `type == "system"`, `subtype == "init"`, and a non-empty `session_id`, return
  `(f"sdk-{session_id}", False)`.
- Else fall back: `f"sdk-{hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]}"`, return
  `(run_id, True)`, and call `_log_self(root, f"no init record in {path}; using fallback id")`.
- Edge case: explicit `--run-id` short-circuits everything; return `(explicit, False)`.

### 7. Add `_translate` and supporting record translators

- One pure function or a small class — recommend a function returning an iterator. Caller
  consumes the iterator and writes; folds `state` and `cost` along the way.
- Implement per-record-type translation per the table in Phase 2 step 5.
- Each event carries: `schema_version`, `event`, `timestamp`, `session_id`, `source: "sdk"`,
  `sdk_source_file`, plus event-specific fields.
- Tool-use back-fill from `user.tool_result` is **deferred** to v0.2+. Document this in the
  module docstring and the spec — `result_summary` and `duration_ms` ship as `null` in v0.1.
- `assistant.thinking` blocks: skipped in v0.1.
- `user.tool_result`-only records: skipped in v0.1.
- Unknown record types: `event: "unknown"` with `raw: <truncated payload>` (mirror
  `_on_unknown` in capture.py).

### 8. Add `_process_one` and `run_tail`

- `_process_one(path: Path, *, run_id, source_name, dry_run, force) -> int` per Phase 2 step 9.
- `run_tail(path: Path, *, run_id, source_name, dry_run, force) -> int` per Phase 2 step 8.
- Path resolution: `Path(path).expanduser().resolve()` once at the top of `run_tail`. After
  that, every internal pass uses the absolute path so `sdk_source_file` and the sha1 fallback
  are stable.
- Directory walk: `sorted(root.rglob("cc_raw_output.jsonl"))` with depth ≤ 5
  (`len(p.relative_to(root).parts) <= 5`).
- Exit codes:
  - `0` happy (including "no files found" and "already ingested").
  - `2` user errors: missing path, `--run-id` with multi-file directory.
  - `1` reserved for unexpected runtime IO failures (e.g. permission denied writing
    `runs/<id>/`).
- On `--dry-run`, never call any writer.

### 9. Wire real handler in `cli.py`

- Replace the placeholder `_run_tail` body with the real call:
  ```python
  return tail.run_tail(
      Path(args.path),
      run_id=args.run_id,
      source_name=args.source_name,
      dry_run=args.dry_run,
      force=args.force,
  )
  ```
- Add `from agentlog import tail` next to the existing `from agentlog import __version__, capture, hooks_install` import.

### 10. Write `tests/test_tail.py`

- Same `tmp_path + monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` pattern as
  `tests/test_capture.py`.
- Test cases (one function each, no parametrize unless natural):
  1. `test_happy_path_writes_unified_schema` — point at `tests/fixtures/sdk_minimal.jsonl`,
     assert `state.json`, `events.jsonl`, `cost.json` exist, and the records have
     `source: "sdk"`, `schema_version: 1`, `sdk_source_file` set.
  2. `test_run_id_derived_with_sdk_prefix` — assert run dir is `runs/sdk-abc-123/`.
  3. `test_run_id_fallback_when_no_init` — pass a JSONL with no `system/init` first line;
     assert run dir is `runs/sdk-<sha1[:12]>/` and `_self.log` has a warning.
  4. `test_explicit_run_id_verbatim` — `--run-id foo` → run dir is `runs/foo/` (no `sdk-` prefix).
  5. `test_idempotent_re_ingest` — run twice, assert second run prints
     "already ingested", returns 0, and `events.jsonl` mtime is unchanged.
  6. `test_force_re_ingests` — run, mutate the fixture (e.g. copy + extend), re-run with
     `--force`, assert `events.jsonl` reflects the new content.
  7. `test_dry_run_writes_nothing` — assert no files under `tmp_path/runs/` after `--dry-run`.
  8. `test_dry_run_against_already_ingested` — first run normal, second `--dry-run`; assert
     stdout says "already ingested" not "would write".
  9. `test_missing_file_returns_rc2` — assert rc==2 and a clear stderr message.
  10. `test_empty_file_logs_and_uses_path_hash` — write `path = tmp_path / "cc_raw_output.jsonl"; path.touch()`; assert rc==0, run dir is `runs/sdk-<sha1[:12]>/`, `events.jsonl` is empty (zero bytes or only-newlines), `state.json` exists.
  11. `test_directory_walk` — create three nested cc_raw_output.jsonl files; assert all three
      produce run dirs and the stdout lists one summary line per file.
  12. `test_empty_directory_prints_message` — directory with no matching files; rc==0,
      stdout contains "no cc_raw_output.jsonl files found".
  13. `test_multi_file_with_run_id_errors` — directory + `--run-id foo`; rc==2.
  14. `test_unknown_record_type_emitted_as_event_unknown` — fixture has `api_retry`; assert one
      `events.jsonl` row has `event: "unknown"`.
  15. `test_truncated_mid_stream` — fixture with `init` but corrupt last line; rc==0,
      `state.truncated == true` (or equivalent flag), parseable events captured.
  16. `test_no_runtime_dep_on_dot_adw` — `from agentlog import tail` does not import anything
      under `.adw/`. Assert via `import sys; assert not any("adw_modules" in m for m in sys.modules)` after a clean import.

### 11. Run the full test suite

- `pytest -q` — confirm the existing 76 tests still pass and the new 16 pass.
- `ruff check src tests` — no findings.
- `mypy --strict src tests` — no findings.

### 12. Manual smoke against real fixture

- `python -m agentlog tail agents/1b4319ab/researcher/cc_raw_output.jsonl`
- Verify with: `ls ~/.agentlog/runs/sdk-*` and `head ~/.agentlog/runs/sdk-*/events.jsonl`.
- Re-run; verify "already ingested" message.
- `python -m agentlog tail agents/1b4319ab/` — verify per-file summary lines for every
  `cc_raw_output.jsonl` under the tree.

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation:

- No `HOW_TO_CREATE_TESTS.md` / `TESTING.md` exists in this repo (verified by `glob **/HOW_TO_CREATE_TESTS.md` and similar). Follow the established patterns in `tests/test_capture.py` and `tests/test_cli_smoke.py`.
- Use `tmp_path` + `monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))` to redirect writes to the test sandbox.
- Type-annotate every test function and fixture per mypy strict (`pytest.CaptureFixture[str]`, `pytest.MonkeyPatch`).
- Never hardcode absolute paths; always derive from `tmp_path` or `Path(__file__).parent / "fixtures"`.

### Unit Tests

- `_derive_run_id` — all three branches (explicit, init present, init absent) and the
  side-effect of `_log_self` on the fallback path.
- `_translate` — given a small in-memory list of records, asserts the exact sequence of event
  dicts yielded. No filesystem.
- `_truncate` — already covered by `tests/test_capture.py` for the capture.py copy; the
  duplicated version in `tail.py` gets a smaller smoke test ensuring identical behavior on a
  representative input.
- `_is_already_ingested` and the `--force` unlink path — narrow filesystem tests with
  `tmp_path`.
- `run_tail` orchestrator branches: missing path, empty directory, single file, directory with
  N files, multi-file + `--run-id` error.

### Integration Tests

- End-to-end with the hand-written `sdk_minimal.jsonl` fixture: invoke `run_tail`, read back
  `state.json` / `events.jsonl` / `cost.json`, assert exact field values.
- CLI smoke: `main(["tail", str(fixture)])` produces the expected exit code; `main(["tail", "--dry-run", str(fixture)])` produces zero writes.
- Cross-source unified schema sanity: after a successful `tail`, the `events.jsonl` record shape
  matches the one written by `capture.dispatch("Stop", ...)` in `test_capture.py` (same
  `schema_version`, same `timestamp` format) — discriminated only by `source`.

### Edge Cases

- Path doesn't exist → rc=2, clean stderr.
- Empty file → fallback path-hash run id, empty `events.jsonl`, rc=0.
- File without `system/init` record (truncated) → fallback path-hash run id, warning in
  `_self.log`, rc=0.
- File with one malformed JSON line in the middle → that line becomes an `event: "unknown"` row
  with the unparseable raw, ingest continues, rc=0.
- Multiple `cc_raw_output.jsonl` files in a directory → each gets its own run dir; per-file
  summary printed.
- `--run-id` supplied with a directory containing >1 file → rc=2.
- `--run-id` supplied with a directory containing exactly 1 file → accepted (use the explicit id).
- Idempotency hit without `--force` → rc=0, "already ingested" message, no writes.
- Idempotency hit with `--force` → existing three files unlinked, fresh writes.
- `--dry-run` against an already-ingested run → "already ingested" message, no "would write"
  output, no writes.
- Run id collision with a hooks-mode UUID (extremely unlikely given `sdk-` prefix) — defensive
  test that documents the prefix discipline.
- Unknown stream-json record `type` (e.g. `api_retry`, `rate_limit_event`) → `event: "unknown"`,
  raw payload preserved.
- Very large file (synthesised >10MB via 200k repeated lines in `tmp_path`) → process completes
  in reasonable time, peak memory does not balloon. *Optional*; mark `@pytest.mark.slow` if
  added.
- `tail` does not import anything under `.adw/` at runtime (regression test for CLAUDE.md
  code-provenance rule).

## Acceptance Criteria

1. `agentlog tail agents/1b4319ab/researcher/cc_raw_output.jsonl` produces
   `~/.agentlog/runs/sdk-<session_id>/{state.json, events.jsonl, cost.json}` from that file.
2. Re-running the same command prints `already ingested ...` and the run dir is byte-unchanged.
3. `agentlog tail agents/1b4319ab/` walks the subtree, ingests every `cc_raw_output.jsonl`
   found, and prints one summary line per file.
4. `agentlog tail --dry-run <file>` parses the file, prints a summary, writes nothing.
5. `agentlog tail /nonexistent/path` exits 2 with a clear stderr message.
6. Every `events.jsonl` record carries `"source":"sdk"`, `schema_version:1`, and
   `sdk_source_file`.
7. `agentlog tail --run-id foo <single-file>` produces `~/.agentlog/runs/foo/` (no `sdk-`
   prefix on user-supplied ids).
8. `agentlog tail --run-id foo <directory-with-2+-files>` exits 2 with a clear stderr message.
9. The existing 76 tests still pass. New `tests/test_tail.py` adds ≥16 tests covering happy
   path, idempotency, `--force`, `--dry-run`, missing file, empty file, directory walk,
   multi-file-with-`--run-id` error, unknown record type, and truncated-mid-stream.
10. `ruff check src tests` and `mypy --strict src tests` are clean.
11. `pyproject.toml dependencies = []` is unchanged. No new runtime imports outside the standard
    library.
12. `src/agentlog/capture.py` is unmodified.
13. No source `cc_raw_output.jsonl` file is edited, renamed, or deleted at any point.
14. `README.md` is NOT touched; user-facing docs are produced separately as
    `docs/feature-0241d756-tail-sdk-sidecar.md` in the docs phase.

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These run during the
build phase — do NOT include pytest, linters, or pipeline runs (those belong to dedicated CI
phases).

- `.venv/bin/python -m py_compile src/agentlog/tail.py && echo "OK"` — verify no syntax errors in the new module.
- `.venv/bin/python -m py_compile src/agentlog/cli.py && echo "OK"` — verify the CLI wiring still parses.
- `.venv/bin/python -m py_compile src/agentlog/_constants.py && echo "OK"` — verify the constant addition.
- `.venv/bin/python -c "from agentlog import tail; print('import OK')"` — verify the new module imports cleanly.
- `.venv/bin/python -c "from agentlog import cli; print('import OK')"` — verify the CLI module imports with the new wiring.
- `.venv/bin/agentlog --help` — verify `tail` appears in the subcommand listing without the "not yet implemented" suffix.
- `.venv/bin/agentlog tail --help` — verify the four flags (`--run-id`, `--source-name`, `--dry-run`, `--force`) are documented.

## Notes

- **No new dependencies.** Everything stdlib: `argparse`, `hashlib`, `json`, `os`, `pathlib`,
  `sys`, `datetime`, `typing`. `pyproject.toml dependencies = []` stays empty.
- **Local-first.** Zero network calls. `tail` reads a local file and writes to
  `$AGENTLOG_HOME` (defaulting to `~/.agentlog/`). This holds CLAUDE.md hard rule #6.
- **Schema discipline.** Every JSONL record carries `schema_version: 1`. Unknown / unparseable
  records get an `event: "unknown"` row with the raw payload — mirrors the hooks-mode
  `_on_unknown` behavior and satisfies CLAUDE.md hard rule #7.
- **Fail-loud contract.** `tail` is a user-invoked CLI, not a Claude Code hot-path hook. It
  surfaces errors via stderr + non-zero rc rather than swallowing them. The fail-open contract
  remains scoped to `capture.run_hook`.
- **`assistant_text` is a new event kind** introduced here. Hooks mode doesn't emit it. The
  three downstream readers (`agentlog ls / cost / view`, ship-scope items #4/#5/#6) must learn
  about it — but that's their problem, not `tail`'s, because they own the unified-schema reader
  surface.
- **Tool-use back-fill is deferred to v0.2+.** SDK split: assistant fires `tool_use` block,
  next user record contains the `tool_result`. v0.1 ships `tool_use` events with
  `result_summary: null` and `duration_ms: null`. A future enrichment pass can buffer pending
  `tool_use_id`s and back-fill. Documented in the module docstring.
- **`assistant.thinking` blocks** are skipped in v0.1 (stay minimal). May surface as
  `assistant_text` with a `kind: "thinking"` field in v0.2+.
- **Code provenance.** The translator pattern is informed by the (private) bbworkflow
  `adw_modules/agent.py` parsers per the CLAUDE.md code-provenance section. The runtime does
  NOT import from `.adw/`; the patterns are lifted and sanitized into `tail.py` directly.
- **Future refactor (out of scope).** If the duplicated helpers between `capture.py` and
  `tail.py` start drifting in ways that bite, extract them into `src/agentlog/_io.py` exposing
  `data_root`, `session_dir`, `append_event`, `write_state`, `write_cost`, `truncate`,
  `isoformat`, `log_self` as public names. Both modules then import from there. Not bundled
  with this feature because the contracts (fail-open vs fail-loud) might legitimately want
  divergent error handling around the writer calls.
- **Deferred to v0.2+ (do NOT scope-creep into v0.1):** live `tail -f` mode, multi-source merge
  (one run dir holding both hooks events and SDK events), `agentlog subprocess(...)` wrapper,
  back-fill of `tool_result` into prior `tool_use` events, `agentlog ls / cost / view`
  awareness of SDK-source runs (those are separate ship-scope items #4/#5/#6).
- **Privacy.** This is a local-first observability tool. `tail` reads files the user already
  has on disk and writes to a directory under the user's `$HOME`. No data leaves the machine.
  Any future opt-in network export (OTEL, v1.0+) will be a separate, explicitly-opted-in
  feature.
