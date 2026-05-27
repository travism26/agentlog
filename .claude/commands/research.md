# Research

Research the codebase to understand architecture, documentation, and impact analysis for upcoming changes.

## Variables

adw_id: $1
prompt: $2

## Instructions

- If adw_id or prompt is not provided, stop and ask the user to provide them
- Research the codebase iteratively:
  1. Find architecture diagrams and documentation (`DESIGN.md`, `CLAUDE.md`, `README.md`, `research/` directory)
  2. Identify relevant source files based on the prompt
  3. Map component dependencies and connections
  4. Analyze what the change will affect
- Create research document at: `ai_docs/research/{adw_id}-{descriptive-name}.md`
  - Replace `{descriptive-name}` with a short, descriptive name based on the research topic (e.g., "auth-system-analysis", "api-structure-mapping", "database-schema-review")
- Use Explore agents for thorough codebase search
- Multiple iterations are expected - be thorough

## Research Process

### Phase 1: Documentation Discovery

- Read `DESIGN.md` (locked v0.1 design — source of truth for scope and architecture)
- Read `CLAUDE.md` (project orientation and non-negotiable hard rules)
- **Read `docs/adw-lessons.md`** — recurring bug patterns from prior ADW runs.
  Note which lessons apply to the prompt's domain and flag them in the research
  output's Recommendations section so the plan phase pre-commits to the
  appropriate test shapes.
- Search for README files across the codebase
- Look in `research/` for prior research notes that may be relevant
- Check `.claude/` for configuration and command patterns

### Phase 2: Source Code Exploration

- Identify files and modules related to the prompt
- Trace imports and dependencies
- Find existing patterns and conventions
- Note any tests that cover the relevant areas

### Phase 3: Impact Analysis

- Determine what will need to change
- Identify dependencies (what depends on affected code)
- Find integration points with other systems
- Note potential risks or considerations

## Research Document Format

```md
# Research: {descriptive title}

## Metadata

adw_id: `{adw_id}`
prompt: `{prompt}`
date: `{YYYY-MM-DD}`

## Executive Summary

<2-3 sentences summarizing findings and key takeaways>

## Existing Architecture

### Relevant Documentation Found

<list of docs found with brief descriptions of what they contain>

### Component Map

<diagram or description of relevant components and their relationships>

### Key Files and Modules

<list of important files with their purpose>

## Affected Areas

### Files That Will Need Changes

<list of files with reasons why they need modification>

### Dependencies

<what depends on these files, what these files depend on>

### Integration Points

<where the affected code connects to other systems>

## Impact Analysis

### Scope of Change

<assessment of how widespread the change will be>

### Risks and Considerations

<potential issues, edge cases, or concerns>

### Existing Patterns to Follow

<patterns in the codebase that should be maintained>

## Recommendations

<suggestions for implementation approach based on research findings>
```

## Report

Return ONLY the relative path to the research document created (e.g., `ai_docs/research/abc12345-auth-system-analysis.md`).

IMPORTANT: Do NOT include any summary, explanation, or additional text. Return only the file path.
