#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis review phase script.

Usage:
  uv run travis_review.py <adw-id>

Reviews implementation against specification. Does NOT automatically resolve
review issues - just reports them.

Outputs:
  - Review summary with issue list (if any)
  - State updated with review results
  - Logs written to agents/{adw_id}/travis_review.log
"""

import sys
import os
import logging
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest
from adw_modules.utils import setup_logger, parse_json
from adw_modules.data_types import ReviewResult
from travis.travis_state import TravisState


def run_review(spec_file: str, adw_id: str, logger: logging.Logger) -> ReviewResult:
    """Run the review using the /review command.

    Args:
        spec_file: Path to spec file
        adw_id: ADW workflow ID
        logger: Logger instance

    Returns:
        ReviewResult object

    Raises:
        ValueError: If review output cannot be parsed
    """
    logger.info(f"Reviewing implementation against spec: {spec_file}")

    request = AgentTemplateRequest(
        agent_name="reviewer",
        slash_command="/review",
        args=[spec_file, adw_id],
        adw_id=adw_id,
        model="sonnet",
        working_dir=os.getcwd()  # Ensure skills discovered from project root
    )

    response = execute_template(request)

    if not response.success:
        logger.error(f"Review command failed: {response.output}")
        raise ValueError(f"Review execution failed: {response.output}")

    # Parse review results
    try:
        review_result = parse_json(response.output, ReviewResult)
        return review_result
    except Exception as e:
        logger.error(f"Failed to parse review results: {e}")
        logger.debug(f"Review output was: {response.output}")
        raise ValueError(f"Failed to parse review results: {e}")


def format_review_summary(review: ReviewResult) -> str:
    """Format review results summary.

    Args:
        review: ReviewResult object

    Returns:
        Formatted summary string
    """
    lines = []
    lines.append("\nReview Summary:")
    lines.append(f"  Status: {'PASSED' if review.success else 'FAILED'}")
    lines.append(f"  Summary: {review.review_summary}")

    if review.review_issues:
        lines.append(f"\n  Issues Found: {len(review.review_issues)}")
        for issue in review.review_issues:
            lines.append(f"\n  Issue #{issue.review_issue_number}:")
            lines.append(f"    Severity: {issue.issue_severity}")
            lines.append(f"    Description: {issue.issue_description}")
            lines.append(f"    Resolution: {issue.issue_resolution}")
            if issue.screenshot_path:
                lines.append(f"    Screenshot: {issue.screenshot_path}")
    else:
        lines.append("\n  No issues found")

    if review.screenshots:
        lines.append(f"\n  Screenshots: {len(review.screenshots)}")
        for screenshot in review.screenshots:
            lines.append(f"    - {screenshot}")

    return "\n".join(lines)


def create_review_issues_file(
    review: ReviewResult,
    spec_file: str,
    adw_id: str,
    logger: logging.Logger
) -> str | None:
    """Create the review issues markdown file if there are any issues.

    Args:
        review: ReviewResult object
        spec_file: Path to the spec file
        adw_id: ADW workflow ID
        logger: Logger instance

    Returns:
        Path to created file, or None if no issues
    """
    if not review.review_issues:
        logger.info("No review issues - skipping review issues file creation")
        return None

    # Create directory if needed
    review_issues_dir = Path("specs/review_issues")
    review_issues_dir.mkdir(parents=True, exist_ok=True)

    # Build the file path
    review_file = review_issues_dir / f"review-{adw_id}.md"

    # Build file content
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "PASSED" if review.success else "FAILED"

    lines = [
        f"# Review Issues - {adw_id}",
        "",
        f"**Spec File:** {spec_file}",
        f"**Review Date:** {current_date}",
        f"**Status:** {status}",
        "",
        "## Summary",
        "",
        review.review_summary,
        "",
        f"## Issues Found: {len(review.review_issues)}",
        "",
    ]

    for issue in review.review_issues:
        lines.extend([
            f"### Issue #{issue.review_issue_number}: {issue.issue_severity}",
            "",
            f"**File:** {issue.screenshot_path or 'N/A'}",
            "",
            "**Description:**",
            issue.issue_description,
            "",
            "**Resolution:**",
            issue.issue_resolution,
            "",
            "---",
            "",
        ])

    content = "\n".join(lines)

    # Write the file
    review_file.write_text(content)
    logger.info(f"Created review issues file: {review_file}")

    return str(review_file)


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_review.py <adw-id>", file=sys.stderr)
        sys.exit(1)

    adw_id = sys.argv[1]

    # Setup logging
    logger = setup_logger(adw_id, "travis_review")
    logger.info(f"Travis Review starting - ADW ID: {adw_id}")

    # Load state
    state = TravisState.load(adw_id)

    # Check if test phase completed
    if not state.has_phase_completed("test"):
        logger.error("Test phase has not completed - run travis_test.py first")
        sys.exit(1)

    # Get spec file from state
    spec_file = state.get("spec_file")
    if not spec_file:
        logger.error("No spec file found in state")
        sys.exit(1)

    # Run review
    try:
        review_result = run_review(spec_file, adw_id, logger)
    except ValueError as e:
        logger.error(f"Review failed: {e}")
        state.set_phase_result("review", {
            "success": False,
            "error": str(e)
        })
        state.save()
        sys.exit(1)

    # Format and print summary
    summary = format_review_summary(review_result)
    logger.info(summary)
    print(summary)

    # Create review issues file if there are issues (deterministic - don't rely on agent)
    review_issues_file = create_review_issues_file(review_result, spec_file, adw_id, logger)
    if review_issues_file:
        print(f"\nReview issues file created: {review_issues_file}")

    # Update state
    state.set_phase_result("review", {
        "success": review_result.success,
        "summary": review_result.review_summary,
        "issue_count": len(review_result.review_issues),
        "issues": [
            {
                "number": issue.review_issue_number,
                "severity": issue.issue_severity,
                "description": issue.issue_description
            }
            for issue in review_result.review_issues
        ],
        "screenshots": review_result.screenshots,
        "review_issues_file": review_issues_file
    })
    state.save()

    # Exit with appropriate code
    if not review_result.success:
        logger.error("Review phase completed with issues")
        sys.exit(1)
    else:
        logger.info("Review phase completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
