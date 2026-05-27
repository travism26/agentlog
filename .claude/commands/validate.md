# Code Quality Validation

Run code quality validation checks using Python tools (Ruff, MyPy, Radon) and return violations in a standardized JSON format.

## Purpose

Enforce code quality standards and best practices across the Python CLI application:

## Variables

VALIDATION_TIMEOUT: 2 minutes

## Instructions

Execute code quality validation using Ruff, MyPy, and Radon, then return combined results as a JSON array of violations.

### Execution Steps

1. **Run Ruff linting**

   ```bash
   ruff check . --output-format=json
   ```

2. **Run MyPy type checking**

   ```bash
   mypy . --show-column-numbers --show-error-codes --no-error-summary
   ```

3. **Run Radon complexity analysis**

   ```bash
   radon cc -a .
   ```

4. **Combine results**
   - Parse output from all validation tools
   - Convert each tool's output format to the standardized ValidationViolation schema
   - Merge into a single JSON array
   - For Radon: only include functions with complexity >= 10 (warning threshold)

5. **Return results**
   - IMPORTANT: Return ONLY the combined JSON array with violations
   - Do not include any additional text, explanations, or markdown formatting
   - The output will be parsed as JSON
   - If any validation tool fails to run, include the error as a violation

## Error Handling

If a validation tool fails to execute:

- Capture the error message
- Include it as a violation in the combined results
- Example:
  ```json
  [
    {
      "rule": "validation-error",
      "file": "unknown",
      "line": null,
      "column": null,
      "severity": "error",
      "message": "Failed to run ruff: <error message>",
      "fix_suggestion": null
    }
  ]
  ```

## Report

Return results exclusively as a JSON array matching the ValidationViolation schema:

### Output Structure

```json
[
  {
    "rule": "string",
    "file": "string",
    "line": number | null,
    "column": number | null,
    "severity": "error" | "warning",
    "message": "string",
    "fix_suggestion": "string" | null
  },
  ...
]
```

### Example Output - No Violations

```json
[]
```

### Example Output - With Violations

```json
[
  {
    "rule": "ruff/F401",
    "file": "agentlog/cli.py",
    "line": 5,
    "column": 1,
    "severity": "error",
    "message": "Module imported but unused: 'sys'",
    "fix_suggestion": "Remove the unused import or use the module in your code."
  },
  {
    "rule": "mypy/error",
    "file": "agentlog/commands/scan.py",
    "line": 45,
    "column": 12,
    "severity": "error",
    "message": "Argument 1 to 'process_target' has incompatible type 'str'; expected 'Target'",
    "fix_suggestion": "Add proper type hints or convert the string to a Target object."
  },
  {
    "rule": "radon/complexity",
    "file": "agentlog/core/scanner.py",
    "line": 78,
    "column": null,
    "severity": "warning",
    "message": "Function 'process_results' has complexity 15 (threshold: 10)",
    "fix_suggestion": "Consider refactoring this function to reduce complexity. Break it into smaller functions."
  }
]
```

## Notes

- Only critical violations (severity: "error") should fail the validation phase
- Warnings (severity: "warning") are informational and don't fail validation
- Ruff provides JSON output natively
- MyPy output needs to be parsed from text format
- Radon complexity scores >= 10 are flagged as warnings
