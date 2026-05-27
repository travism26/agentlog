#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis documentation phase script.

Usage:
  uv run travis_document.py <adw-id>

Generates feature documentation from spec file.

Outputs:
  - Documentation file path or "No documentation needed"
  - State updated with documentation results
  - Logs written to agents/{adw_id}/travis_document.log
"""

import sys
import os
import logging
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest
from adw_modules.utils import setup_logger, parse_json
from adw_modules.data_types import DocumentationResult
from travis.travis_state import TravisState


def run_documentation(spec_file: str, adw_id: str, logger: logging.Logger) -> DocumentationResult:
    """Run the documentation using the /document command.

    Args:
        spec_file: Path to spec file
        adw_id: ADW workflow ID
        logger: Logger instance

    Returns:
        DocumentationResult object

    Raises:
        ValueError: If documentation output cannot be parsed
    """
    logger.info(f"Generating documentation from spec: {spec_file}")

    request = AgentTemplateRequest(
        agent_name="documenter",
        slash_command="/document",
        args=[spec_file],
        adw_id=adw_id,
        model="sonnet",
        working_dir=os.getcwd()  # Ensure skills discovered from project root
    )

    response = execute_template(request)

    if not response.success:
        logger.error(f"Documentation command failed: {response.output}")
        raise ValueError(f"Documentation execution failed: {response.output}")

    # Parse documentation results
    try:
        doc_result = parse_json(response.output, DocumentationResult)
        return doc_result
    except Exception as e:
        logger.error(f"Failed to parse documentation results: {e}")
        logger.debug(f"Documentation output was: {response.output}")
        raise ValueError(f"Failed to parse documentation results: {e}")


def format_documentation_summary(doc_result: DocumentationResult) -> str:
    """Format documentation results summary.

    Args:
        doc_result: DocumentationResult object

    Returns:
        Formatted summary string
    """
    lines = []
    lines.append("\nDocumentation Summary:")
    lines.append(f"  Status: {'SUCCESS' if doc_result.success else 'FAILED'}")

    if doc_result.documentation_created:
        lines.append("  Documentation Created: Yes")
        if doc_result.documentation_path:
            lines.append(f"  Path: {doc_result.documentation_path}")
    else:
        lines.append("  Documentation Created: No")
        if doc_result.error_message:
            lines.append(f"  Reason: {doc_result.error_message}")

    return "\n".join(lines)


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_document.py <adw-id>", file=sys.stderr)
        sys.exit(1)

    adw_id = sys.argv[1]

    # Setup logging
    logger = setup_logger(adw_id, "travis_document")
    logger.info(f"Travis Document starting - ADW ID: {adw_id}")

    # Load state
    state = TravisState.load(adw_id)

    # Note: Documentation can run after build, doesn't require review to pass
    if not state.has_phase_completed("build"):
        logger.error("Build phase has not completed - run travis_build.py first")
        sys.exit(1)

    # Get spec file from state
    spec_file = state.get("spec_file")
    if not spec_file:
        logger.error("No spec file found in state")
        sys.exit(1)

    # Run documentation
    try:
        doc_result = run_documentation(spec_file, adw_id, logger)
    except ValueError as e:
        logger.error(f"Documentation failed: {e}")
        state.set_phase_result("document", {
            "success": False,
            "error": str(e)
        })
        state.save()
        sys.exit(1)

    # Format and print summary
    summary = format_documentation_summary(doc_result)
    logger.info(summary)
    print(summary)

    # Update state
    state.set_phase_result("document", {
        "success": doc_result.success,
        "documentation_created": doc_result.documentation_created,
        "documentation_path": doc_result.documentation_path,
        "error_message": doc_result.error_message
    })
    state.save()

    # Always exit with success - documentation is optional
    logger.info("Documentation phase completed")
    sys.exit(0)


if __name__ == "__main__":
    main()
