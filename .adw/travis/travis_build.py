#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis build (implementation) phase script.

Usage:
  uv run travis_build.py <adw-id>

Loads plan from state and executes /implement command to build the feature.

Outputs:
  - Implementation complete confirmation
  - State updated with build results
  - Logs written to agents/{adw_id}/travis_build.log
"""

import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest
from adw_modules.utils import setup_logger
from travis.travis_state import TravisState


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_build.py <adw-id>", file=sys.stderr)
        sys.exit(1)

    adw_id = sys.argv[1]

    # Setup logging
    logger = setup_logger(adw_id, "travis_build")
    logger.info(f"Travis Build starting - ADW ID: {adw_id}")

    # Load state
    state = TravisState.load(adw_id)

    # Check if plan phase completed
    if not state.has_phase_completed("plan"):
        logger.error("Plan phase has not completed - run travis_plan.py first")
        sys.exit(1)

    # Get plan file from state
    plan_file = state.get("plan_file")
    if not plan_file:
        logger.error("No plan file found in state")
        sys.exit(1)

    logger.info(f"Implementing plan: {plan_file}")

    # Execute implementation agent
    request = AgentTemplateRequest(
        agent_name="builder",
        slash_command="/implement",
        args=[plan_file],
        adw_id=adw_id,
        model="sonnet",
        working_dir=os.getcwd()  # Ensure skills discovered from project root
    )

    logger.info("Executing implementation agent...")
    response = execute_template(request)

    if not response.success:
        logger.error(f"Implementation failed: {response.output}")
        state.set_phase_result("build", {
            "success": False,
            "error": response.output
        })
        state.save()
        sys.exit(1)

    # Update state
    state.set_phase_result("build", {
        "success": True,
        "message": "Implementation completed"
    })
    state.save()

    logger.info("Build phase completed successfully")
    print("Implementation complete")


if __name__ == "__main__":
    main()
