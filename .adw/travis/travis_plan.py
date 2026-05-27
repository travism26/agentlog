#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis planning phase script.

Usage:
  uv run travis_plan.py <prompt-or-spec-file> [adw-id] [--plan-type TYPE] [--model MODEL]

If prompt provided: Executes /feature, /chore, or /bug command to create implementation plan
If spec file provided: Uses existing spec (skips planning)

Options:
  --plan-type TYPE: Type of plan to create (feature|chore|bug, default: auto-detect)
  --model MODEL: Model to use for planning (sonnet|opus|haiku, default: sonnet)

Outputs:
  - Plan file path in specs/ directory
  - State saved to agents/{adw_id}/travis_state.json
  - Logs written to agents/{adw_id}/travis_plan.log
"""

import sys
import os
import re
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.agent import execute_template, AgentTemplateRequest
from adw_modules.utils import setup_logger, check_env_vars, make_adw_id
from travis.travis_state import TravisState


def is_spec_file(input_str: str) -> bool:
    """Check if input is a spec file path.

    Args:
        input_str: Input string to check

    Returns:
        True if it looks like a spec file path
    """
    # CHANGED: Only treat files in specs/ directory as existing spec files.
    # Previously, any .md file with a path separator was treated as a spec file,
    # which incorrectly caught files like tasks/*.md that should be treated as
    # planning inputs rather than pre-existing specs.
    # Now we only accept files that explicitly start with "specs/" to avoid ambiguity.
    if input_str.startswith("specs/"):
        print(f"Detected spec file input skipping planning phase: {input_str}")
        return True

    return False


def determine_slash_command(prompt: str, plan_type: Optional[str] = None) -> str:
    """Determine which slash command to use based on prompt content or explicit type.

    Args:
        prompt: User prompt text
        plan_type: Explicit plan type (feature|chore|bug) or None for auto-detect

    Returns:
        Slash command to use (/feature, /chore, or /bug)
    """
    # If explicit plan type provided, use it
    if plan_type:
        return f"/{plan_type}"

    # Otherwise, auto-detect based on keywords
    feature_keywords = ["feature", "implement", "add", "create", "new", "build"]
    chore_keywords = ["chore", "refactor", "update", "improve", "cleanup"]
    bug_keywords = ["bug", "fix", "error", "issue", "broken", "failing"]

    prompt_lower = prompt.lower()

    # Count keyword matches
    feature_count = sum(1 for kw in feature_keywords if kw in prompt_lower)
    chore_count = sum(1 for kw in chore_keywords if kw in prompt_lower)
    bug_count = sum(1 for kw in bug_keywords if kw in prompt_lower)

    # Choose command with most keyword matches
    if bug_count > max(feature_count, chore_count):
        return "/bug"
    elif chore_count > feature_count:
        return "/chore"
    return "/feature"


def extract_plan_file_path(output: str) -> str:
    """Extract plan file path from agent output.

    Args:
        output: Agent response text

    Returns:
        Plan file path

    Raises:
        ValueError: If no valid path found
    """
    # Look for specs/ pattern
    # Supports: specs/{type}-{adw-id}-description.md
    # or: specs/{type}-N-adw-{adw-id}-description.md
    match = re.search(
        r"(specs/(?:feature|chore|bug)-(?:\d+-)?(?:adw-)?[a-f0-9]+-[^\s]+\.md)",
        output
    )

    if match:
        return match.group(1)

    # Look for any .md file in specs/
    match = re.search(r"(specs/[^\s]+\.md)", output)
    if match:
        return match.group(1)

    raise ValueError("No plan file path found in agent output")


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_plan.py <prompt-or-spec-file> [adw-id] [--plan-type TYPE] [--model MODEL]", file=sys.stderr)
        sys.exit(1)

    # Parse arguments
    input_arg = sys.argv[1]
    adw_id = None
    plan_type = None  # None means auto-detect
    model = "opus"  # Default to opus for planning (best model for spec creation)

    # Process optional arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--plan-type":
            if i + 1 < len(sys.argv):
                plan_type = sys.argv[i + 1]
                if plan_type not in ["feature", "chore", "bug"]:
                    print(f"Error: Invalid plan-type value: {plan_type}. Must be 'feature', 'chore', or 'bug'", file=sys.stderr)
                    sys.exit(1)
                i += 2
            else:
                print("Error: --plan-type requires a value (feature|chore|bug)", file=sys.stderr)
                sys.exit(1)
        elif arg == "--model":
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
    logger = setup_logger(adw_id, "travis_plan")
    logger.info(f"Travis Plan starting - ADW ID: {adw_id}")

    # Validate environment
    check_env_vars(logger)

    # Load or create state
    state = TravisState.load(adw_id)

    # Check if input is a spec file or prompt
    if is_spec_file(input_arg):
        # Use existing spec file
        logger.info(f"Using existing spec file: {input_arg}")

        # Verify file exists
        if not os.path.exists(input_arg):
            logger.error(f"Spec file not found: {input_arg}")
            sys.exit(1)

        state.update(spec_file=input_arg, plan_file=input_arg)
        state.set_phase_result("plan", {
            "success": True,
            "file": input_arg,
            "message": "Using existing spec file"
        })
        state.save()

        logger.info(f"Plan phase complete - using existing spec: {input_arg}")
        print(input_arg)
        sys.exit(0)

    # Input is a prompt - execute planning command
    prompt = input_arg
    logger.info(f"Creating plan from prompt: {prompt[:100]}...")

    # Determine which slash command to use
    slash_command = determine_slash_command(prompt, plan_type)
    if plan_type:
        logger.info(f"Using explicit plan type: {plan_type} -> {slash_command}")
    else:
        logger.info(f"Auto-detected slash command: {slash_command}")

    # Execute planning agent.
    # /feature, /chore, /bug all expect two positional args: $1=adw_id, $2=prompt.
    # execute_template joins args with spaces, so the order here matters.
    request = AgentTemplateRequest(
        agent_name="planner",
        slash_command=slash_command,
        args=[adw_id, prompt],
        adw_id=adw_id,
        model=model,
        working_dir=os.getcwd()  # Ensure skills discovered from project root
    )

    logger.info("Executing planning agent...")
    response = execute_template(request)

    if not response.success:
        logger.error(f"Planning failed: {response.output}")
        state.set_phase_result("plan", {
            "success": False,
            "error": response.output
        })
        state.save()
        sys.exit(1)

    # Extract plan file path from response
    try:
        plan_file = extract_plan_file_path(response.output)
        logger.info(f"Agent reported plan path: {plan_file}")
    except ValueError as e:
        logger.error(f"Could not extract plan file path: {e}")
        logger.debug(f"Agent output was: {response.output}")
        state.set_phase_result("plan", {
            "success": False,
            "error": str(e)
        })
        state.save()
        sys.exit(1)

    # Verify plan file exists before declaring success. The agent can report
    # a path without actually writing the file (e.g. when it asks for
    # clarification instead of planning) — don't claim "Plan created" until
    # we've checked the filesystem.
    if not os.path.exists(plan_file):
        logger.error(f"Plan file not found at reported path: {plan_file}")
        state.set_phase_result("plan", {
            "success": False,
            "error": f"Plan file not found: {plan_file}"
        })
        state.save()
        sys.exit(1)

    # Now that the file exists on disk, it's safe to declare success.
    logger.info(f"Plan created: {plan_file}")

    # Update state
    state.update(plan_file=plan_file, spec_file=plan_file)
    state.set_phase_result("plan", {
        "success": True,
        "file": plan_file,
        "slash_command": slash_command
    })
    state.save()

    logger.info("Plan phase completed successfully")
    print(plan_file)


if __name__ == "__main__":
    main()
