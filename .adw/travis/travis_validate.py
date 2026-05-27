#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis validation phase script with automatic retry logic.

Usage:
  uv run travis_validate.py <adw-id> [OPTIONS]

Options:
  --max-retries N       Maximum retry attempts (default: 3)
  --warnings-only       Report warnings only, don't fail on violations

Outputs:
  - Validation summary with violation counts
  - State updated with validation results
  - Logs written to agents/{adw_id}/travis_validate.log
"""

import sys
import json
import logging
from pathlib import Path
from typing import List, Tuple, Dict
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest, AgentPromptResponse
from adw_modules.utils import setup_logger, parse_json
from adw_modules.data_types import ValidationViolation
from travis.travis_state import TravisState

# Max parallel resolution workers
MAX_PARALLEL_RESOLVERS = 5

# Default max retry attempts
DEFAULT_MAX_RETRIES = 3


def parse_validation_results(
    output: str, logger: logging.Logger
) -> Tuple[List[ValidationViolation], int, int]:
    """Parse validation results JSON and return (violations, critical_count, warning_count)."""
    try:
        raw = parse_json(output)
        violations = [ValidationViolation.model_validate(item) for item in raw]
        critical_count = sum(1 for v in violations if v.severity == "error")
        warning_count = sum(1 for v in violations if v.severity == "warning")
        return violations, critical_count, warning_count
    except Exception as e:
        logger.error(f"Error parsing validation results: {e}")
        return [], 0, 0


def run_validation(
    adw_id: str,
    logger: logging.Logger
) -> AgentPromptResponse:
    """Run the validation suite using the /validate command.

    Args:
        adw_id: ADW workflow ID
        logger: Logger instance

    Returns:
        AgentPromptResponse with validation results
    """
    # Get project root directory (where .mcp.json is located)
    project_root = Path(__file__).parent.parent.parent

    request = AgentTemplateRequest(
        agent_name="validator",
        slash_command="/validate",
        args=[],
        adw_id=adw_id,
        model="sonnet",
        working_dir=str(project_root)
    )

    logger.info("Running architectural validation...")
    return execute_template(request)


def resolve_violations_for_file(
    file: str,
    violations: List[ValidationViolation],
    adw_id: str,
    logger: logging.Logger,
    iteration: int,
    file_index: int
) -> int:
    """Resolve all violations for a single file in one agent call.

    Args:
        file: File path with violations
        violations: All violations in this file
        adw_id: ADW workflow ID
        logger: Logger instance
        iteration: Current retry iteration
        file_index: Index of this file in the batch

    Returns:
        Number of violations resolved (len(violations) on success, 0 on failure)
    """
    rules = ", ".join(sorted({v.rule for v in violations}))
    logger.info(f"Resolving {len(violations)} violation(s) in {file} [{rules}]")

    # Pass all violations as a JSON array so the agent reads the file once
    violations_payload = json.dumps([v.model_dump() for v in violations], indent=2)
    agent_name = f"validation_resolver_iter{iteration}_file{file_index}"

    request = AgentTemplateRequest(
        agent_name=agent_name,
        slash_command="/resolve_validation_violation",
        args=[violations_payload],
        adw_id=adw_id,
        model="sonnet"
    )

    response = execute_template(request)

    if response.success:
        logger.info(f"Successfully resolved {len(violations)} violation(s) in {file}")
        return len(violations)
    else:
        logger.error(f"Failed to resolve violations in {file}: {response.output[:200]}")
        return 0


def run_validation_with_resolution(
    adw_id: str,
    logger: logging.Logger,
    max_retries: int,
    warnings_only: bool = False
) -> Tuple[List[ValidationViolation], int, int, int]:
    """Run validation with automatic resolution and retry logic.

    Args:
        adw_id: ADW workflow ID
        logger: Logger instance
        max_retries: Maximum retry attempts
        warnings_only: If True, only report warnings without failing

    Returns:
        Tuple of (violations, critical_count, warning_count, attempts)
    """
    attempt = 0
    violations: list[ValidationViolation] = []
    critical_count = 0
    warning_count = 0

    while attempt < max_retries:
        attempt += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"Validation Run Attempt {attempt}/{max_retries}")
        logger.info(f"{'='*60}\n")

        # Run validation
        validation_response = run_validation(adw_id, logger)

        # Check for execution errors
        if not validation_response.success:
            logger.error(f"Error running validation: {validation_response.output}")
            return [], 0, 0, attempt

        # Parse validation results
        violations, critical_count, warning_count = parse_validation_results(
            validation_response.output, logger
        )

        logger.info(f"Validation results: {critical_count} critical, {warning_count} warnings")

        # If warnings-only mode, treat as success
        if warnings_only:
            logger.info("Running in warnings-only mode - treating as success")
            return violations, critical_count, warning_count, attempt

        # If no critical violations, we're done
        if critical_count == 0:
            logger.info("No critical violations found!")
            return violations, critical_count, warning_count, attempt

        # If this is the last attempt, don't try to resolve
        if attempt == max_retries:
            logger.info(f"Reached maximum retry attempts ({max_retries})")
            return violations, critical_count, warning_count, attempt

        # Try to resolve critical violations — group by file, run in parallel
        logger.info(f"\nAttempting to resolve {critical_count} critical violation(s)...")
        critical_violations = [v for v in violations if v.severity == "error"]

        # Group by file so each agent reads/edits one file exactly once
        by_file: Dict[str, List[ValidationViolation]] = defaultdict(list)
        for v in critical_violations:
            by_file[v.file].append(v)

        logger.info(
            f"Grouped into {len(by_file)} file(s), "
            f"running up to {MAX_PARALLEL_RESOLVERS} in parallel..."
        )

        resolved_count = 0
        workers = min(len(by_file), MAX_PARALLEL_RESOLVERS)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    resolve_violations_for_file,
                    file, file_violations, adw_id, logger, attempt, idx
                ): file
                for idx, (file, file_violations) in enumerate(by_file.items())
            }
            for future in as_completed(futures):
                file = futures[future]
                try:
                    resolved_count += future.result()
                except Exception as exc:
                    logger.error(f"Exception resolving {file}: {exc}")

        logger.info(f"Resolved {resolved_count}/{len(critical_violations)} critical violation(s)")

        # Continue to next iteration to re-run validation
        if resolved_count > 0:
            logger.info("Re-running validation after resolution...")
        else:
            logger.warning("No violations were resolved, but continuing to retry...")

    return violations, critical_count, warning_count, attempt


def format_validation_summary(
    violations: List[ValidationViolation],
    critical_count: int,
    warning_count: int,
    attempts: int
) -> str:
    """Format validation results summary."""
    lines = []
    lines.append(f"\nValidation Summary (after {attempts} attempt(s)):")
    lines.append(f"  Critical: {critical_count}")
    lines.append(f"  Warnings: {warning_count}")
    lines.append(f"  Total:    {len(violations)}")

    if critical_count > 0:
        lines.append("\nCritical violations:")
        for violation in violations:
            if violation.severity == "error":
                lines.append(f"  - {violation.rule} in {violation.file}:{violation.line}")
                lines.append(f"    {violation.message}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    load_dotenv()

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: uv run travis_validate.py <adw-id> [OPTIONS]", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print("  --max-retries N       Maximum retry attempts (default: 3)", file=sys.stderr)
        print("  --warnings-only       Report warnings only, don't fail on violations", file=sys.stderr)
        sys.exit(1)

    adw_id = sys.argv[1]
    max_retries = DEFAULT_MAX_RETRIES
    warnings_only = False

    # Parse flags
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == "--max-retries":
            if i + 1 >= len(sys.argv):
                print("Error: --max-retries requires a value", file=sys.stderr)
                sys.exit(1)
            try:
                max_retries = int(sys.argv[i + 1])
                i += 2
            except ValueError:
                print(f"Error: Invalid max-retries value: {sys.argv[i + 1]}", file=sys.stderr)
                sys.exit(1)

        elif arg == "--warnings-only":
            warnings_only = True
            i += 1

        else:
            print(f"Error: Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)

    # Setup logging
    logger = setup_logger(adw_id, "travis_validate")
    logger.info(f"Travis Validation starting - ADW ID: {adw_id}")
    logger.info(f"Max retries: {max_retries}")
    logger.info(f"Warnings only: {warnings_only}")

    # Load state
    state = TravisState.load(adw_id)

    # Check if build phase completed
    if not state.has_phase_completed("build"):
        logger.error("Build phase has not completed - run travis_build.py first")
        sys.exit(1)

    # Run validation with resolution
    violations, critical_count, warning_count, attempts = run_validation_with_resolution(
        adw_id, logger, max_retries, warnings_only
    )

    # Format and print summary
    summary = format_validation_summary(violations, critical_count, warning_count, attempts)
    logger.info(summary)
    print(summary)

    # Update state
    state.set_phase_result("validate", {
        "success": critical_count == 0 or warnings_only,
        "critical": critical_count,
        "warnings": warning_count,
        "total": len(violations),
        "attempts": attempts
    })
    state.save()

    # Exit with appropriate code
    if critical_count > 0 and not warnings_only:
        logger.error("Validation phase completed with critical violations")
        sys.exit(1)
    else:
        logger.info("Validation phase completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
