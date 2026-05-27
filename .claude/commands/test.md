# Test

Run the agentlog test suite and report results in a JSON contract the ADW test phase can parse and act on.

## Variables

adw_id: $1

## Instructions

### 1. Discover the test layout

- Tests live under `tests/` at the repo root
- The project uses `pytest` (configured in `pyproject.toml` under `[tool.pytest.ini_options]`)
- The package is installed editable; run pytest from the venv: `.venv/bin/python -m pytest`

### 2. Decide what to run

Default behavior is to run the full suite:

```bash
.venv/bin/python -m pytest -q
```

If the current branch's diff (`git diff origin/main --name-only`) is narrowly scoped to a single subsystem, you MAY run a focused subset first to get fast feedback — for example:

```bash
.venv/bin/python -m pytest tests/test_cli_smoke.py -q
```

After any scoped run, ALWAYS run the full suite to confirm no regressions.

### 3. Interpret results

- Treat the full-suite run as authoritative.
- Capture failures with their full pytest error message so the resolver phase has enough context to act.
- Do NOT mark a test as `passed: false` for warnings, deprecations, or skipped tests — only actual failures or errors.

### 4. Hard rules (from CLAUDE.md)

- Tests for hook handlers MUST verify the fail-open contract (handler exits 0 even on exception).
- Tests MUST NOT mutate the user's real `~/.claude/settings.json`. Use `tmp_path` fixtures.
- Performance-sensitive tests should assert the budget where practical, but treat assertion failures as `tech_debt` rather than blocking unless wildly out of bounds.

## Output

Return ONLY a JSON array of test results, no surrounding prose, no markdown fences. The array MUST conform to the following schema (consumed by `adw_modules.data_types.TestResult`):

```json
[
  {
    "test_name": "string — short, stable identifier (e.g., 'tests/test_cli_smoke.py' or 'full_suite')",
    "passed": true,
    "execution_command": "string — the exact pytest invocation used",
    "test_purpose": "string — one sentence on what this run covered",
    "error": null
  }
]
```

When a run fails, set `"passed": false` and include the pytest error excerpt in `"error"`. Always include at least one entry representing the full suite run.

## Report

Output the JSON array. Nothing else. The ADW pipeline calls `json.loads` on your output — any non-JSON text breaks the test phase.
