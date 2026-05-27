# Resolve Validation Violation

Fix a specific code quality or architectural validation violation by applying the minimal, targeted changes required to resolve the issue.

## Purpose

Automatically resolve violations detected by the `/validate` command by:

- Analyzing the specific violation (tool, file, location, message)
- Understanding the context and current code structure
- Applying tool-specific fixes that maintain code quality standards
- Verifying the fix resolves the violation

## Arguments

This command accepts either a **single violation object** or a **JSON array of violation objects** for the same file:

```json
[
  {
    "tool": "ruff" | "mypy" | "radon" | "bandit" | "import-linter",
    "rule": "string",
    "file": "string",
    "line": number | null,
    "column": number | null,
    "severity": "error" | "warning",
    "message": "string",
    "fix_suggestion": "string" | null
  }
]
```

When given an array, all violations belong to the same file — read the file once and fix all violations in a single editing pass before verifying.

## Instructions

### 1. Analyze the Violation(s)

Parse the provided JSON argument — it may be a single violation object or an array. For each violation, understand:
- **Tool**: Which validation tool detected the violation
- **Rule**: Which specific rule was violated
- **File**: The file containing the violation (all violations in an array share the same file)
- **Line/Column**: Exact location of the violation
- **Message**: Specific violation details
- **Fix Suggestion**: Guidance on how to fix (if available)

### 2. Context Discovery

Read the affected file **once** (even when fixing multiple violations) to understand:
- Current code structure around the violation
- What imports/dependencies are being used
- What the code is trying to accomplish
- The architectural layer the file belongs to

### 3. Determine Fix Strategy

Based on the tool and rule violated, apply the appropriate fix:

#### For Ruff Violations

Common Ruff issues and fixes:

1. **Unused imports** (F401)
   - Remove the unused import statement
   - Verify no other code relies on it

2. **Undefined names** (F821)
   - Add missing imports
   - Check for typos in variable/function names

3. **Import ordering** (I001)
   - Reorder imports: stdlib, third-party, local
   - Group and sort alphabetically within each section

4. **Line too long** (E501)
   - Break long lines appropriately
   - Use parentheses for implicit line continuation

5. **Unused variables** (F841)
   - Remove unused variables
   - Prefix with `_` if intentionally unused

#### For MyPy Violations

Common MyPy issues and fixes:

1. **Missing type hints**
   - Add type annotations to function signatures
   - Example: `def process(data):` → `def process(data: dict[str, Any]) -> None:`

2. **Incompatible types**
   - Fix type mismatches
   - Add proper type conversions
   - Use type guards if needed

3. **Untyped function call**
   - Add type hints to the called function
   - Use `# type: ignore` only as last resort with comment explaining why

4. **Optional errors**
   - Add None checks before accessing optional values
   - Use proper Optional[] type hints

5. **import-not-found / import-untyped**
   - Add `# type: ignore[import-not-found]` or `# type: ignore[import-untyped]` on the import line
   - Or add a `[[tool.mypy.overrides]]` entry in `pyproject.toml` for the specific package
   - **NEVER add `src/agentlog/py.typed`** — this marker signals a fully-typed published library and cascades into hundreds of new `import-untyped` errors for all third-party deps

#### For Radon Violations

Common complexity issues and fixes:

1. **High cyclomatic complexity** (>10)
   - Extract complex conditions into separate functions
   - Use early returns to reduce nesting
   - Break function into smaller functions
   - Consider using match/case statements

2. **Deep nesting** (>3 levels)
   - Extract nested logic into helper functions
   - Use early returns to reduce nesting
   - Flatten conditionals where possible

#### For Bandit Violations

Common security issues and fixes:

1. **Hardcoded credentials** (B105, B106)
   - Move credentials to environment variables
   - Use configuration management

2. **subprocess with shell=True** (B602)
   - Change to `shell=False`
   - Pass command as list instead of string

3. **Unsafe deserialization** (B301, B403)
   - Use `json.load()` instead of `pickle.load()`
   - Use `yaml.safe_load()` instead of `yaml.load()`

4. **Use of eval/exec** (B307)
   - Replace with safer alternatives
   - Use ast.literal_eval() for literal evaluation

#### For Import-Linter Violations

Architectural layer violations:

1. **CLI importing from infrastructure**
   - Move logic to application layer
   - CLI should only import from application layer

2. **Domain importing from infrastructure**
   - Extract interface/protocol in domain
   - Implement in infrastructure
   - Use dependency injection

3. **Circular dependencies**
   - Extract shared code to separate module
   - Use protocols/interfaces to break cycles
   - Refactor to clarify dependency direction

### 4. Apply the Fix

Make the minimal necessary changes:
- Edit only the affected file(s)
- Keep changes focused on resolving the specific violation
- Don't refactor unrelated code
- Maintain code style and patterns from the file
- Preserve PEP8 compliance
- Maintain existing type hints

### 5. Validate the Fix

After applying changes, run the appropriate validation command:

**For Ruff violations:**
```bash
ruff check .
```

**For MyPy violations:**
```bash
mypy .
```

**For Radon violations:**
```bash
radon cc -s -a .
```

**For Bandit violations:**
```bash
bandit -r .
```

**For Import-Linter violations:**
```bash
lint-imports
```

Verify:
1. The specific violation no longer appears
2. No new violations were introduced
3. The fix maintains code quality standards

## Report Format

After fixing the violation, provide a concise report:

```markdown
### Violation Resolved

**Tool**: <tool-name>
**Rule**: <rule-code>
**File**: <file-path>:<line>

**Changes Made**:
- <Brief description of what was changed>
- <Brief description of what was changed>

**Verification**:
- Re-ran validation: [PASS/FAIL]
- Violation resolved: [YES/NO]
- Quality standards maintained: [YES/NO]
```

## Examples

### Example 1: Fixing Ruff unused import

**Violation**:
```json
{
  "tool": "ruff",
  "rule": "F401",
  "file": "agentlog/cli/commands.py",
  "line": 5,
  "column": 1,
  "severity": "error",
  "message": "`os` imported but unused",
  "fix_suggestion": "Remove the unused import"
}
```

**Fix Steps**:
1. Read `agentlog/cli/commands.py` to confirm `os` is not used
2. Remove the line `import os`
3. Run `ruff check .` to verify fix

### Example 2: Fixing MyPy missing type hints

**Violation**:
```json
{
  "tool": "mypy",
  "rule": "no-untyped-def",
  "file": "agentlog/application/workflow.py",
  "line": 15,
  "column": 1,
  "severity": "error",
  "message": "Function is missing a type annotation",
  "fix_suggestion": "Add type hints to function signature"
}
```

**Fix Steps**:
1. Read `agentlog/application/workflow.py` to understand function signature
2. Add appropriate type hints: `def process_workflow(data: dict[str, Any]) -> WorkflowResult:`
3. Add necessary imports: `from typing import Any`
4. Run `mypy .` to verify fix

### Example 3: Fixing Radon complexity violation

**Violation**:
```json
{
  "tool": "radon",
  "rule": "CC",
  "file": "agentlog/domain/validator.py",
  "line": 20,
  "column": 1,
  "severity": "warning",
  "message": "Function 'validate_submission' has complexity 15 (> 10)",
  "fix_suggestion": "Break down into smaller functions"
}
```

**Fix Steps**:
1. Read `agentlog/domain/validator.py` to understand the function
2. Extract complex conditional logic into separate helper functions
3. Use early returns to reduce nesting
4. Run `radon cc -s -a .` to verify complexity is now ≤ 10

### Example 4: Fixing Bandit security issue

**Violation**:
```json
{
  "tool": "bandit",
  "rule": "B602",
  "file": "agentlog/infrastructure/scanner.py",
  "line": 42,
  "column": 5,
  "severity": "error",
  "message": "subprocess call with shell=True identified, security issue",
  "fix_suggestion": "Use shell=False and pass command as list"
}
```

**Fix Steps**:
1. Read `agentlog/infrastructure/scanner.py` to see subprocess usage
2. Change `subprocess.run(cmd, shell=True)` to `subprocess.run(cmd.split(), shell=False)`
3. Or use list directly: `subprocess.run(['nmap', '-sV', target], shell=False)`
4. Run `bandit -r .` to verify fix

## Notes

- Fix all provided violations in a single editing pass (read file once, apply all fixes, verify once)
- Don't make changes beyond what's needed to fix the violations
- Maintain PEP8 compliance and code quality standards
- Preserve existing type hints and add new ones where needed
- For architectural violations, respect the layer boundaries (cli → application → domain, infrastructure)
- Always verify the fix by re-running the appropriate validation tool
- If the fix introduces new violations, adjust the approach
- When extracting functions to reduce complexity, maintain the same level of type safety
- For security issues, never compromise on the fix - security must be addressed properly
