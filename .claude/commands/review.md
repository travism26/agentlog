# Review

Follow the `Instructions` below to **review work done against a specification file** (specs/\*.md) to ensure implemented features match requirements. Use the spec file to understand the requirements and use git diff to understand the changes made. If there are issues, report them; if not, report success.

## Variables

adw_id: $ARGUMENT
spec_file: $ARGUMENT
agent_name: $ARGUMENT if provided, otherwise use 'review_agent'

## Instructions

- Check current git branch using `git branch` to understand context
- Run `git diff origin/main` to see all changes made in current branch. Continue even if there are no changes related to the spec file.
- Find the spec file by looking for specs/\*.md files in the diff that match the current branch name
- Read the identified spec file to understand requirements
- IMPORTANT: We're reviewing a Python implementation. Focus on:
  - Code correctness and adherence to spec
  - Proper error handling
  - Test coverage for new functionality
  - CLI/module behavior matches spec expectations
  - Proper Python patterns and conventions
- **IMPORTANT: read `docs/adw-lessons.md` BEFORE reviewing.** It captures recurring
  bug patterns surfaced during prior ADW runs. For each numbered lesson, scan the
  diff and flag any matching issue. Cite the lesson number in your `issue_description`
  (e.g., "Lesson #1: sort key without direction-asserting test"). High-leverage
  checks specifically — do not skip these:
  - **Lesson #1 — sort keys:** does any function in the diff produce ordered output?
    If yes, verify there's a test that asserts the position of one row relative
    to another in the dimension being sorted. Missing test → flag as `skippable`.
  - **Lesson #2 — module-level asserts:** grep the diff for `^assert ` at indent 0
    (module scope). Any hit is a fail-open violation → flag as `tech_debt`.
  - **Lesson #3 — SQLite schema bootstrap order:** if the diff adds or modifies
    SQLite schema management, verify the version-check runs BEFORE any incompatible
    table is touched. Misordering → flag as `tech_debt`.
  - **Lesson #4 — stale future comments:** grep the diff for comments containing
    `future|will replace|TODO|FIXME|will land in|deferred to` and verify each is
    still accurate after the diff. Stale → flag as `skippable`.
  - **Lesson #5 — durable installed-format strings:** if a constant is being
    renamed and that constant appears in any file outside `src/agentlog/` (settings.json,
    JSONL records, sqlite columns), flag as `blocker` and demand a migration plan.
  - **Lesson #7 — fail-open boundary integrity:** if the diff modifies code under
    a `try/except Exception` fail-open boundary, verify the recovery path itself
    cannot raise (e.g., `_log_self` calls wrapped in `contextlib.suppress`).
  - **Lesson #8 — stylistic vs spec conflicts:** if you're about to flag use of
    `collections.abc.Callable`, `contextlib.suppress`, or any stdlib idiom that
    `ruff` actively prefers (UP035, SIM105, etc.), DON'T. Those are not bugs.
  - **Lesson #11 — regression test naming:** for any reviewer-found bug, the
    fix MUST add a named regression test describing the contract being asserted.
    Missing test → re-flag the original bug as `blocker` since "fixed" without a
    test means "fixed silently" which means "next refactor re-breaks it."
  Lessons #6, #9, #10 are also worth scanning, but the seven above are the
  highest-leverage / most-frequently-violated.
- IMPORTANT: Issue Severity Guidelines
  - Think hard about the impact of the issue on the feature and the user
  - Guidelines:
    - `skippable` - the issue is non-blocker for the work to be released but is still a problem
    - `tech_debt` - the issue is non-blocker for the work to be released but will create technical debt that should be addressed in the future
    - `blocker` - the issue is a blocker for the work to be released and should be addressed immediately. It will harm the user experience or will not function as expected.
- IMPORTANT: Return ONLY the JSON object with review results
  - IMPORTANT: Output your result as a single JSON object based on the `Report` section below.
  - IMPORTANT: Do not include any additional text, explanations, or markdown formatting
  - Your ENTIRE response must be valid JSON starting with `{` and ending with `}` — nothing else
  - We immediately run JSON.parse() on the output, so any non-JSON text will cause the review phase to FAIL
- Ultra think as you work through the review process. Focus on the critical functionality and code quality. Don't report issues if they are not critical to the feature.

## Validation Steps

Run these commands synchronously (foreground only — do NOT use background tasks):

1. Review changed files directly for correctness against the spec

> Note: Tests are run during the `/test` phase — do NOT re-run the test suite here. Set `tests_passed: true` unless you observed an explicit test failure.

## Report

- IMPORTANT: Return results exclusively as a JSON object based on the `Output Structure` section below.
- `success` should be `true` if there are NO BLOCKING issues (implementation matches spec for critical functionality)
- `success` should be `false` ONLY if there are BLOCKING issues that prevent the work from being released
- `review_issues` can contain issues of any severity (skippable, tech_debt, or blocker)
- This allows subsequent agents to quickly identify and resolve blocking errors while documenting all issues

## Review Issues File

- If there are ANY issues found (regardless of severity), create a review issues file:
  - Create the directory `specs/review_issues/` if it doesn't exist
  - Create a file named `specs/review_issues/review-{adw_id}.md` where {adw_id} is the workflow ID
  - Use the Write tool to create this file with the following structure:

    ```markdown
    # Review Issues - {adw_id}

    **Spec File:** {spec_file}
    **Review Date:** {current_date}
    **Status:** {PASSED or FAILED based on success field}

    ## Summary

    {review_summary}

    ## Issues Found: {count}

    {For each issue, create a section like:}

    ### Issue #{review_issue_number}: {issue_severity}

    **File:** {file_path}

    **Description:**
    {issue_description}

    **Resolution:**
    {issue_resolution}

    ---
    ```

- If there are NO issues, do NOT create the review issues file

### Output Structure

```json
{
  "success": "boolean - true if there are NO BLOCKING issues (can have skippable/tech_debt issues), false if there are BLOCKING issues",
  "review_summary": "string - 2-4 sentences describing what was built and whether it matches the spec. Written as if reporting during a standup meeting.",
  "review_issues": [
    {
      "review_issue_number": "number - the issue number based on the index of this issue",
      "issue_description": "string - description of the issue",
      "issue_resolution": "string - description of the resolution",
      "issue_severity": "string - severity of the issue between 'skippable', 'tech_debt', 'blocker'"
    }
  ],
  "tests_passed": "boolean - always true here; tests run in the /test phase, not during review"
}
```
