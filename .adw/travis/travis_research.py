#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis research phase script.

Usage:
  uv run travis_research.py <prompt> [adw-id] [--model MODEL]

Executes /research command to analyze the codebase before planning.

Options:
  --model MODEL: Model to use for research (sonnet|opus|haiku, default: sonnet)

Outputs:
  - Research file path in ai_docs/research/ directory
  - State saved to agents/{adw_id}/travis_state.json
  - Logs written to agents/{adw_id}/travis_research.log
"""

import sys
import os
import re
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest
from adw_modules.utils import setup_logger, check_env_vars, make_adw_id
from travis.travis_state import TravisState


def extract_research_file_path(output: str) -> str:
    """Extract research file path from agent output.

    Args:
        output: Agent response text

    Returns:
        Research file path

    Raises:
        ValueError: If no valid path found
    """
    # Look for ai_docs/research/ pattern
    # Supports: ai_docs/research/{adw_id}-{descriptive-name}.md
    match = re.search(
        r"(ai_docs/research/[a-f0-9]+-[^\s]+\.md)",
        output
    )

    if match:
        return match.group(1)

    # Look for any .md file in ai_docs/research/
    match = re.search(r"(ai_docs/research/[^\s]+\.md)", output)
    if match:
        return match.group(1)

    raise ValueError("No research file path found in agent output")


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_research.py <prompt> [adw-id] [--model MODEL]", file=sys.stderr)
        sys.exit(1)

    # Parse arguments
    prompt = sys.argv[1]
    adw_id = None
    model = "sonnet"  # Default to sonnet

    # Process optional arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--model":
            if i + 1 < len(sys.argv):
                model = sys.argv[i + 1]
                if model not in ["sonnet", "opus", "haiku"]:
                    print(f"Error: Invalid model value: {model}. Must be 'sonnet', 'opus', or 'haiku'", file=sys.stderr)
                    sys.exit(1)
                i += 2
            else:
                print("Error: --model requires a value (sonnet|opus|haiku)", file=sys.stderr)
                sys.exit(1)
        elif not adw_id and not arg.startswith("--"):
            # This is the adw_id
            adw_id = arg
            i += 1
        else:
            print(f"Error: Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)

    # Generate adw_id if not provided
    if not adw_id:
        adw_id = make_adw_id()

    # Setup logging
    logger = setup_logger(adw_id, "travis_research")
    logger.info(f"Travis Research starting - ADW ID: {adw_id}")

    # Validate environment
    check_env_vars(logger)

    # Load or create state
    state = TravisState.load(adw_id)

    # Execute research command
    logger.info(f"Researching codebase for: {prompt[:100]}...")

    # Execute research agent
    request = AgentTemplateRequest(
        agent_name="researcher",
        slash_command="/research",
        args=[adw_id, prompt],
        adw_id=adw_id,
        model=model,
        working_dir=os.getcwd()  # Ensure skills discovered from project root
    )

    logger.info("Executing research agent...")
    response = execute_template(request)

    if not response.success:
        logger.error(f"Research failed: {response.output}")
        state.set_phase_result("research", {
            "success": False,
            "error": response.output
        })
        state.save()
        sys.exit(1)

    # Extract research file path from response
    try:
        research_file = extract_research_file_path(response.output)
        logger.info(f"Research document created: {research_file}")
    except ValueError as e:
        logger.error(f"Could not extract research file path: {e}")
        logger.debug(f"Agent output was: {response.output}")
        state.set_phase_result("research", {
            "success": False,
            "error": str(e)
        })
        state.save()
        sys.exit(1)

    # Verify research file exists
    if not os.path.exists(research_file):
        logger.error(f"Research file not found: {research_file}")
        state.set_phase_result("research", {
            "success": False,
            "error": f"Research file not found: {research_file}"
        })
        state.save()
        sys.exit(1)

    # Update state
    state.update(research_file=research_file)
    state.set_phase_result("research", {
        "success": True,
        "file": research_file
    })
    state.save()

    logger.info("Research phase completed successfully")
    print(research_file)


if __name__ == "__main__":
    main()
