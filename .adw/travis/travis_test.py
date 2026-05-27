#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis test phase script with intelligent test selection and automatic retry logic.

Usage:
  uv run travis_test.py <adw-id> [OPTIONS]

Options:
  --max-retries N       Maximum retry attempts (default: 3)
  --run-all-tests       Force running all tests (bypasses intelligent selection)
  --diff-base REF       Git reference to compare against (default: HEAD)
  --with-browser        Enable Chrome DevTools MCP for E2E/browser testing
  --mcp-config FILE     Use a specific MCP config file (e.g., .mcp-testing.json)

Intelligent test selection:
  - Analyzes git changes since specified base
  - Runs only tests affected by changed modules
  - Automatically runs full suite for core file changes
  - Use --run-all-tests to force complete test suite

MCP Configuration:
  - By default, no MCP servers are loaded (uses empty .mcp.json)
  - Use --with-browser to load Chrome DevTools for E2E tests
  - Use --mcp-config to specify a custom MCP config file

Outputs:
  - Test summary with pass/fail status
  - State updated with test results and selection strategy
  - Logs written to agents/{adw_id}/travis_test.log
"""

import sys
import logging
from pathlib import Path
from typing import List, Tuple, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest, AgentPromptResponse
from adw_modules.utils import setup_logger, parse_json
from adw_modules.data_types import TestResult
from travis.travis_state import TravisState

# Default max retry attempts
DEFAULT_MAX_RETRIES = 3


def parse_test_results(
    output: str, logger: logging.Logger
) -> Tuple[List[TestResult], int, int]:
    """Parse test results JSON and return (results, passed_count, failed_count)."""
    try:
        results = parse_json(output, List[TestResult])  # type: ignore[type-var]
        passed_count = sum(1 for test in results if test.passed)
        failed_count = len(results) - passed_count
        return results, passed_count, failed_count
    except Exception as e:
        logger.error(f"Error parsing test results: {e}")
        return [], 0, 0


def run_tests(
    adw_id: str,
    logger: logging.Logger,
    run_all_tests: bool = False,
    diff_base: str = "origin/main",
    mcp_config: Optional[str] = None
) -> AgentPromptResponse:
    """Run the test suite using the /test command with intelligent selection.

    Args:
        adw_id: ADW workflow ID
        logger: Logger instance
        run_all_tests: If True, force running all tests
        diff_base: Git reference to compare against
        mcp_config: Path to MCP config file (e.g., .mcp-browser.json)

    Returns:
        AgentPromptResponse with test results
    """
    # Build args for /test command
    args = []
    if run_all_tests:
        args.append("--run-all-tests")
    if diff_base:
        args.extend(["--diff-base", diff_base])

    # Get project root directory (where .mcp.json is located)
    project_root = Path(__file__).parent.parent.parent

    request = AgentTemplateRequest(
        agent_name="test_runner",
        slash_command="/test",
        args=args,
        adw_id=adw_id,
        model="sonnet",
        working_dir=str(project_root),
        mcp_config=mcp_config
    )

    selection_msg = "all tests" if run_all_tests else f"intelligent selection (diff: {diff_base})"
    mcp_msg = f" with MCP: {mcp_config}" if mcp_config else ""
    logger.info(f"Running test suite with {selection_msg}{mcp_msg}...")
    return execute_template(request)


def resolve_failed_test(
    test: TestResult,
    adw_id: str,
    logger: logging.Logger,
    iteration: int,
    test_index: int,
    mcp_config: Optional[str] = None
) -> bool:
    """Attempt to resolve a single failed test.

    Args:
        test: Failed test result
        adw_id: ADW workflow ID
        logger: Logger instance
        iteration: Current retry iteration
        test_index: Index of test in failed tests list
        mcp_config: Path to MCP config file (e.g., .mcp-browser.json)

    Returns:
        True if resolution succeeded
    """
    logger.info(f"Resolving failed test: {test.test_name}")

    # Create payload for the resolve command
    test_payload = test.model_dump_json(indent=2)

    # Create agent name with iteration
    agent_name = f"test_resolver_iter{iteration}_{test_index}"

    # Get project root directory
    project_root = Path(__file__).parent.parent.parent

    # Execute resolution
    request = AgentTemplateRequest(
        agent_name=agent_name,
        slash_command="/resolve_failed_test",
        args=[test_payload],
        adw_id=adw_id,
        model="sonnet",
        working_dir=str(project_root),
        mcp_config=mcp_config
    )

    response = execute_template(request)

    if response.success:
        logger.info(f"Successfully resolved: {test.test_name}")
        return True
    else:
        logger.error(f"Failed to resolve: {test.test_name}")
        return False


def run_tests_with_resolution(
    adw_id: str,
    logger: logging.Logger,
    max_retries: int,
    run_all_tests: bool = False,
    diff_base: str = "origin/main",
    mcp_config: Optional[str] = None
) -> Tuple[List[TestResult], int, int, int]:
    """Run tests with automatic resolution and retry logic.

    Args:
        adw_id: ADW workflow ID
        logger: Logger instance
        max_retries: Maximum retry attempts
        run_all_tests: If True, force running all tests
        diff_base: Git reference to compare against
        mcp_config: Path to MCP config file (e.g., .mcp-browser.json)

    Returns:
        Tuple of (results, passed_count, failed_count, attempts)
    """
    attempt = 0
    results: list[TestResult] = []
    passed_count = 0
    failed_count = 0

    while attempt < max_retries:
        attempt += 1
        logger.info(f"\n{'='*60}")
        logger.info(f"Test Run Attempt {attempt}/{max_retries}")
        logger.info(f"{'='*60}\n")

        # Run tests
        test_response = run_tests(adw_id, logger, run_all_tests, diff_base, mcp_config)

        # Check for execution errors
        if not test_response.success:
            logger.error(f"Error running tests: {test_response.output}")
            return [], 0, 0, attempt

        # Parse test results
        results, passed_count, failed_count = parse_test_results(
            test_response.output, logger
        )

        logger.info(f"Test results: {passed_count} passed, {failed_count} failed")

        # If all tests passed, we're done
        if failed_count == 0:
            logger.info("All tests passed!")
            return results, passed_count, failed_count, attempt

        # If this is the last attempt, don't try to resolve
        if attempt == max_retries:
            logger.info(f"Reached maximum retry attempts ({max_retries})")
            return results, passed_count, failed_count, attempt

        # Try to resolve failed tests
        logger.info(f"\nAttempting to resolve {failed_count} failed test(s)...")
        failed_tests = [test for test in results if not test.passed]

        resolved_count = 0
        for idx, test in enumerate(failed_tests):
            if resolve_failed_test(test, adw_id, logger, attempt, idx, mcp_config):
                resolved_count += 1

        logger.info(f"Resolved {resolved_count}/{len(failed_tests)} failed test(s)")

        # Continue to next iteration to re-run tests
        if resolved_count > 0:
            logger.info("Re-running tests after resolution...")
        else:
            logger.warning("No tests were resolved, but continuing to retry...")

    return results, passed_count, failed_count, attempt


def format_test_summary(
    results: List[TestResult],
    passed_count: int,
    failed_count: int,
    attempts: int
) -> str:
    """Format test results summary."""
    lines = []
    lines.append(f"\nTest Summary (after {attempts} attempt(s)):")
    lines.append(f"  Passed: {passed_count}")
    lines.append(f"  Failed: {failed_count}")
    lines.append(f"  Total:  {len(results)}")

    if failed_count > 0:
        lines.append("\nFailed tests:")
        for test in results:
            if not test.passed:
                lines.append(f"  - {test.test_name}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    load_dotenv()

    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: uv run travis_test.py <adw-id> [OPTIONS]", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print("  --max-retries N       Maximum retry attempts (default: 3)", file=sys.stderr)
        print("  --run-all-tests       Force running all tests", file=sys.stderr)
        print("  --diff-base REF       Git reference to compare against (default: HEAD)", file=sys.stderr)
        print("  --with-browser        Enable Chrome DevTools MCP for E2E/browser testing", file=sys.stderr)
        print("  --mcp-config FILE     Use a specific MCP config file", file=sys.stderr)
        sys.exit(1)

    adw_id = sys.argv[1]
    max_retries = DEFAULT_MAX_RETRIES
    run_all_tests = False
    diff_base = "origin/main"
    mcp_config: Optional[str] = None

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

        elif arg == "--run-all-tests":
            run_all_tests = True
            i += 1

        elif arg == "--diff-base":
            if i + 1 >= len(sys.argv):
                print("Error: --diff-base requires a value", file=sys.stderr)
                sys.exit(1)
            diff_base = sys.argv[i + 1]
            i += 2

        elif arg == "--with-browser":
            mcp_config = ".mcp-browser.json"
            i += 1

        elif arg == "--mcp-config":
            if i + 1 >= len(sys.argv):
                print("Error: --mcp-config requires a value", file=sys.stderr)
                sys.exit(1)
            mcp_config = sys.argv[i + 1]
            i += 2

        else:
            print(f"Error: Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)

    # Setup logging
    logger = setup_logger(adw_id, "travis_test")
    logger.info(f"Travis Test starting - ADW ID: {adw_id}")
    logger.info(f"Max retries: {max_retries}")
    logger.info(f"Run all tests: {run_all_tests}")
    logger.info(f"Diff base: {diff_base}")
    logger.info(f"MCP config: {mcp_config or 'None (default)'}")

    # Load state
    state = TravisState.load(adw_id)

    # Check if build phase completed
    if not state.has_phase_completed("build"):
        logger.error("Build phase has not completed - run travis_build.py first")
        sys.exit(1)

    # Run tests with resolution
    results, passed_count, failed_count, attempts = run_tests_with_resolution(
        adw_id, logger, max_retries, run_all_tests, diff_base, mcp_config
    )

    # Format and print summary
    summary = format_test_summary(results, passed_count, failed_count, attempts)
    logger.info(summary)
    print(summary)

    # Update state
    state.set_phase_result("test", {
        "success": failed_count == 0,
        "passed": passed_count,
        "failed": failed_count,
        "total": len(results),
        "attempts": attempts,
        "mcp_config": mcp_config
    })
    state.save()

    # Exit with appropriate code
    if failed_count > 0:
        logger.error("Test phase completed with failures")
        sys.exit(1)
    else:
        logger.info("Test phase completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
