---
description: Plan a new feature for the agentlog project using the provided prompt and the codebase. Create a comprehensive implementation plan in the `specs/` directory.
---

# Feature Planning

Create a plan to implement the feature using the specified markdown `Plan Format`. Research the codebase and create a thorough plan.

## Variables

adw_id: $1
prompt: $2

## Instructions

- If the adw_id or prompt is not provided, stop and ask the user to provide them.
- Create a plan to implement the feature described in the `prompt`
- The plan should be comprehensive, well-designed, and follow existing patterns
- Create the plan in the `specs/` directory with filename: `feature-{adw_id}-{descriptive-name}.md`
  - Replace `{descriptive-name}` with a short, descriptive name based on the feature (e.g., "add-session-capture", "implement-cost-rollup", "create-tail-mode")

**Research Integration**:

- Check if `ai_docs/research/{adw_id}-*.md` exists
- If found, read and incorporate findings into the plan
- Reference the research document in the Relevant Files section

- Research the codebase starting with `README.md`, `DESIGN.md`, and `CLAUDE.md`
- **Read `docs/adw-lessons.md` BEFORE drafting the plan.** It catalogs recurring
  bug patterns from prior ADW runs and includes copy-paste-ready "Test shape"
  blocks for each. If your feature touches any of these patterns (sort keys,
  schema versioning, fail-open boundaries, sentinel strings, dispatch tables,
  etc.), copy the relevant Test shape into your plan's Testing Strategy section
  verbatim — don't rediscover them at review time.
- Replace every <placeholder> in the `Plan Format` with the requested value
- Use your reasoning model: THINK HARD about the feature requirements, design, and implementation approach
- Follow existing patterns and conventions in the codebase
- Design for extensibility and maintainability
- Honor the hard rules in `CLAUDE.md` (hook latency budget, fail-open, no auto-install, local-first, schema versioning, idempotent init)
- Stay within v0.1 ship scope unless the feature is explicitly authorized for a later version

## Relevant Files

- `README.md` - Project overview (start here)
- `DESIGN.md` - Locked v0.1 design document — the source of truth for scope, architecture, and hard rules
- `CLAUDE.md` - Project orientation and non-negotiable constraints
- `docs/adw-lessons.md` - Recurring bug patterns from prior ADW runs (sort keys, schema bootstrap, fail-open boundaries, etc.) — apply BEFORE writing the spec
- `ai_docs/research/` - Research documents from pre-planning analysis (check for `{adw_id}-*.md`)
- `src/agentlog/` - Main package source code
  - `cli.py` - CLI entry point (subcommands: init, uninstall, tail, ls, cost, view)
  - `__init__.py` - Package version
- `tests/` - Test suite
- `.claude/commands/` - Claude command templates
- `specs/` - Specification and plan documents

**Documentation to Check**:

- When your plan includes creating tests, search for testing documentation files (e.g., `HOW_TO_CREATE_TESTS.md`, `TESTING.md`) in the relevant directories before writing tests

## Plan Format

```md
# Feature: <feature name>

## Metadata

adw_id: `{adw_id}`
prompt: `{prompt}`

## Feature Description

<describe the feature in detail, including its purpose and value to users>

## User Story

As a <developer using AI coding agents / platform engineer / AI researcher>
I want to <action/goal>
So that <benefit/value>

## Problem Statement

<clearly define the specific problem or opportunity this feature addresses>

## Solution Statement

<describe the proposed solution approach and how it solves the problem>

## Relevant Files

Use these files to implement the feature:

<list files relevant to the feature with bullet points explaining why. Include new files to be created under an h3 'New Files' section if needed>

## Implementation Plan

### Phase 1: Foundation

<describe the foundational work needed before implementing the main feature>

### Phase 2: Core Implementation

<describe the main implementation work for the feature>

### Phase 3: Integration

<describe how the feature will integrate with existing functionality>

## Step by Step Tasks

IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers with bullet points. Start with foundational changes then move to specific changes. Include creating tests throughout the implementation process>

### 1. <First Task Name>

- <specific action>
- <specific action>

### 2. <Second Task Name>

- <specific action>
- <specific action>

<continue with additional tasks as needed>

## Testing Strategy

**IMPORTANT**: Before creating tests, check for testing documentation:

- Look for files like `HOW_TO_CREATE_TESTS.md`, `TESTING.md`, or `README.md` in the relevant test directory
- Follow existing patterns and use available test helpers/utilities
- Use centralized configuration (never hardcode paths, model names, or environment-specific values)

### Unit Tests

<describe unit tests needed for the feature, including handler behavior, parsing utilities, and CLI surface>

### Integration Tests

<describe integration tests needed for end-to-end workflows (hook -> capture -> read), CLI subcommands, and filesystem interactions>

### Edge Cases

<list edge cases that need to be tested — e.g., malformed hook payloads, concurrent sessions, missing settings.json>

## Acceptance Criteria

<list specific, measurable criteria that must be met for the feature to be considered complete>

## Compile Checks

Fast checks to verify the implementation has no syntax or import errors. These run during the build phase — do NOT include pytest, linters, or pipeline runs (those belong to dedicated CI phases).

<list only fast, side-effect-free commands: py_compile, import smoke tests, --help flags>
- Example: `.venv/bin/python -m py_compile src/agentlog/cli.py && echo "OK"` - Verify no syntax errors
- Example: `.venv/bin/python -c "from agentlog import cli; print('import OK')"` - Verify module imports cleanly
- Example: `.venv/bin/agentlog --help` - Verify CLI still works

## Notes

<optional additional context, future considerations, or dependencies. If new libraries are needed, specify using `uv add`. Note any privacy implications (this is a local-first observability tool — opt-in only for any network/export feature)>
```

## Feature

Use the feature description from the `prompt` variable.

## Report

Return ONLY the relative path to the plan file created (e.g., `specs/feature-8-9dfe4a36-description.md`).

IMPORTANT: Do NOT include any summary, explanation, or additional text. Return only the file path.
