#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis SDLC orchestrator - runs complete local workflow.

Usage:
  uv run travis_sdlc.py <prompt-or-spec-file> [adw-id] [--plan-type TYPE] [--model MODEL] [--skip-research] [--skip-review] [--skip-document] [--skip-validate] [--max-test-retries N] [--max-validation-retries N] [--validate-warnings-only] [--with-browser] [--mcp-config FILE] [--resume]

Executes the complete SDLC workflow locally:
0. Research - Analyze codebase before planning (optional, unless --skip-research)
1. Plan - Create implementation plan
2. Build - Implement the plan
3. Validate - Run architectural validation (unless --skip-validate)
4. Test - Run tests with automatic retry (up to 3 times by default)
5. Review - Review implementation (unless --skip-review)
6. Document - Generate docs (unless --skip-document)

Options:
  --plan-type TYPE: Type of plan to create (feature|chore|bug, default: feature)
  --model MODEL: Model to use for planning (sonnet|opus, default: sonnet)
  --skip-research: Skip the research phase
  --skip-review: Skip the review phase
  --skip-document: Skip the documentation phase
  --skip-validate: Skip the validation phase
  --max-test-retries N: Set maximum test retry attempts (default: 3)
  --max-validation-retries N: Set maximum validation retry attempts (default: 3)
  --validate-warnings-only: Run validation in report-only mode (don't fail on violations)
  --with-browser: Enable Chrome DevTools MCP for E2E/browser testing
  --mcp-config FILE: Use a specific MCP config file for testing
  --resume: Resume from the last completed phase (requires adw-id)

Outputs:
  - Progress logging to console
  - Final summary with all phase results
  - State saved to agents/{adw_id}/travis_state.json
  - Logs written to agents/{adw_id}/travis_sdlc.log

Exit codes:
  0 - All phases succeeded
  1 - One or more phases failed
"""

import sys
import subprocess
from pathlib import Path
from typing import Tuple, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from adw_modules.utils import setup_logger, make_adw_id
from adw_modules.agent import parse_jsonl_output, convert_jsonl_to_json, save_last_entry_as_raw_result
from travis.travis_state import TravisState

# Ordered list of all phases for resume logic
PHASE_ORDER = ["research", "plan", "build", "validate", "test", "review", "document"]


def recover_phase_from_jsonl(adw_id: str, phase: str, agent_name: str, state: TravisState) -> bool:
    """Check if a phase's JSONL output shows success even though state wasn't saved.

    This handles the edge case where Claude Code completed successfully but the
    parent process hung (e.g., stdin blocking) before it could save state.

    Args:
        adw_id: The ADW workflow ID
        phase: Phase name (e.g., "build")
        agent_name: Agent name used for output directory (e.g., "builder")
        state: TravisState to update if recovery succeeds

    Returns:
        True if recovery succeeded and state was updated
    """
    project_root = Path(__file__).parent.parent.parent
    jsonl_path = project_root / "agents" / adw_id / agent_name / "cc_raw_output.jsonl"

    if not jsonl_path.exists():
        return False

    _, result_message = parse_jsonl_output(str(jsonl_path))
    if not result_message:
        return False

    if result_message.get("type") == "result" and result_message.get("subtype") == "success" and not result_message.get("is_error", False):
        # JSONL shows success — recover by saving state
        state.set_phase_result(phase, {
            "success": True,
            "message": f"Recovered from JSONL (duration: {result_message.get('duration_ms', 0)}ms)"
        })

        # Also run the post-processing that was missed
        json_file = convert_jsonl_to_json(str(jsonl_path))
        save_last_entry_as_raw_result(json_file)

        state.save()
        return True

    return False


def get_resume_phase(state: TravisState) -> Optional[str]:
    """Determine which phase to resume from based on state.

    Returns the name of the first phase that has NOT completed successfully,
    or None if all phases are complete.
    """
    for phase in PHASE_ORDER:
        result = state.get_phase_result(phase)
        if not result or not result.get("success"):
            return phase
    return None


def print_phase_header(phase_name: str) -> None:
    """Print a formatted phase header."""
    print(f"\n{'='*70}")
    print(f"  {phase_name}")
    print(f"{'='*70}\n")


def print_phase_result(phase_name: str, success: bool, message: str = "") -> None:
    """Print a formatted phase result."""
    status = "✅ SUCCESS" if success else "❌ FAILED"
    print(f"\n{phase_name}: {status}")
    if message:
        print(f"  {message}")


def run_phase(
    script_name: str,
    args: list,
    phase_name: str,
    required: bool = True
) -> Tuple[bool, Optional[str]]:
    """Run a phase script and return (success, error_message).

    Args:
        script_name: Name of the script to run (e.g., "travis_plan.py")
        args: Arguments to pass to the script
        phase_name: Display name of the phase
        required: Whether failure should stop the workflow

    Returns:
        Tuple of (success, error_message)
    """
    print_phase_header(phase_name)

    # Build command
    script_path = Path(__file__).parent / script_name
    cmd = ["uv", "run", str(script_path)] + args

    # Run the script
    result = subprocess.run(
        cmd,
        capture_output=False,  # Let output go to console
        text=True
    )

    success = result.returncode == 0

    if success:
        print_phase_result(phase_name, True)
    else:
        error_msg = f"Script exited with code {result.returncode}"
        print_phase_result(phase_name, False, error_msg)
        if required:
            return False, error_msg

    return True, None


def print_final_summary(state: TravisState) -> None:
    """Print final summary of all phases."""
    print(f"\n{'='*70}")
    print("  FINAL SUMMARY")
    print(f"{'='*70}\n")

    print(f"ADW ID: {state.adw_id}")
    print(f"Research File: {state.get('research_file', 'N/A')}")
    print(f"Plan File: {state.get('plan_file', 'N/A')}")
    print()

    phase_results = state.data.get("phase_results", {})

    # Research phase
    research = phase_results.get("research", {})
    if research:
        status = "✅ SUCCESS" if research.get("success") else "❌ FAILED"
        print(f"0. Research: {status}")
        if research.get("file"):
            print(f"   File: {research['file']}")

    # Plan phase
    plan = phase_results.get("plan", {})
    if plan:
        print(f"1. Plan:     {'✅ SUCCESS' if plan.get('success') else '❌ FAILED'}")
        if plan.get("file"):
            print(f"   File: {plan['file']}")

    # Build phase
    build = phase_results.get("build", {})
    if build:
        print(f"2. Build:    {'✅ SUCCESS' if build.get('success') else '❌ FAILED'}")

    # Validate phase
    validate = phase_results.get("validate", {})
    if validate:
        status = "✅ SUCCESS" if validate.get("success") else "❌ FAILED"
        print(f"3. Validate: {status}")
        if "critical" in validate:
            print(f"   Critical: {validate['critical']}, Warnings: {validate['warnings']}, Attempts: {validate['attempts']}")

    # Test phase
    test = phase_results.get("test", {})
    if test:
        status = "✅ SUCCESS" if test.get("success") else "❌ FAILED"
        print(f"4. Test:     {status}")
        if "passed" in test:
            print(f"   Passed: {test['passed']}, Failed: {test['failed']}, Attempts: {test['attempts']}")

    # Review phase
    review = phase_results.get("review", {})
    if review:
        status = "✅ SUCCESS" if review.get("success") else "❌ FAILED"
        print(f"5. Review:   {status}")
        if "issue_count" in review:
            print(f"   Issues: {review['issue_count']}")

    # Document phase
    document = phase_results.get("document", {})
    if document:
        status = "✅ SUCCESS" if document.get("success") else "❌ FAILED"
        print(f"6. Document: {status}")
        if document.get("documentation_created"):
            print(f"   Path: {document.get('documentation_path', 'N/A')}")

    print()


def main():
    """Main entry point."""
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: uv run travis_sdlc.py <prompt-or-spec-file> [adw-id] [--plan-type TYPE] [--model MODEL] [--skip-research] [--skip-review] [--skip-document] [--skip-validate] [--max-test-retries N] [--max-validation-retries N] [--validate-warnings-only] [--with-browser] [--mcp-config FILE] [--resume]", file=sys.stderr)
        sys.exit(1)

    # Parse arguments
    input_arg = sys.argv[1]
    adw_id = None
    plan_type = "feature"  # Default to feature
    model = "opus"  # Default to opus for planning/research phases (sonnet used for build)
    skip_research = False
    skip_review = False
    skip_document = False
    skip_validate = False # Default Skip validation
    max_test_retries = 3
    max_validation_retries = 3
    validate_warnings_only = False
    mcp_config: Optional[str] = None
    resume_mode = False

    # Process optional arguments
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--resume":
            resume_mode = True
            i += 1
        elif arg == "--plan-type":
            if i + 1 < len(sys.argv):
                plan_type = sys.argv[i + 1]
                if plan_type not in ["feature", "chore", "bug"]:
                    print(f"Error: Invalid plan-type value: {plan_type}. Must be 'feature', 'chore', or 'bug'", file=sys.stderr)
                    sys.exit(1)
                i += 2
            else:
                print("Error: --plan-type requires a value (feature|chore|bug)", file=sys.stderr)
                sys.exit(1)
        elif arg == "--skip-research":
            skip_research = True
            i += 1
        elif arg == "--skip-review":
            skip_review = True
            i += 1
        elif arg == "--skip-document":
            skip_document = True
            i += 1
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
        elif arg == "--skip-validate":
            skip_validate = True
            i += 1
        elif arg == "--validate-warnings-only":
            validate_warnings_only = True
            i += 1
        elif arg == "--max-validation-retries":
            if i + 1 < len(sys.argv):
                try:
                    max_validation_retries = int(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Error: Invalid max-validation-retries value: {sys.argv[i + 1]}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("Error: --max-validation-retries requires a value", file=sys.stderr)
                sys.exit(1)
        elif arg == "--max-test-retries":
            if i + 1 < len(sys.argv):
                try:
                    max_test_retries = int(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Error: Invalid max-test-retries value: {sys.argv[i + 1]}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("Error: --max-test-retries requires a value", file=sys.stderr)
                sys.exit(1)
        elif arg == "--with-browser":
            mcp_config = ".mcp-browser.json"
            i += 1
        elif arg == "--mcp-config":
            if i + 1 < len(sys.argv):
                mcp_config = sys.argv[i + 1]
                i += 2
            else:
                print("Error: --mcp-config requires a value", file=sys.stderr)
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
        if resume_mode:
            print("Error: --resume requires an adw-id argument", file=sys.stderr)
            sys.exit(1)
        adw_id = make_adw_id()

    # Setup logging
    logger = setup_logger(adw_id, "travis_sdlc")
    logger.info(f"Travis SDLC starting - ADW ID: {adw_id}")
    logger.info(f"Plan type: {plan_type}, Model: {model}")
    logger.info(f"Skip research: {skip_research}, Skip review: {skip_review}, Skip document: {skip_document}, Skip validate: {skip_validate}")
    logger.info(f"Max test retries: {max_test_retries}, Max validation retries: {max_validation_retries}")
    logger.info(f"Validate warnings only: {validate_warnings_only}")
    logger.info(f"MCP config: {mcp_config or 'None (default)'}")
    logger.info(f"Resume mode: {resume_mode}")

    print(f"\n{'='*70}")
    print("  Travis SDLC Workflow")
    print(f"  ADW ID: {adw_id}")
    if resume_mode:
        print("  Mode: RESUME")
    print(f"{'='*70}\n")

    # Resume logic: determine which phases to skip
    resume_from_phase: Optional[str] = None
    if resume_mode:
        state = TravisState.load(adw_id)
        phase_results = state.data.get("phase_results", {})

        if not phase_results:
            print("No completed phases found in state — starting from scratch")
            logger.info("Resume: no completed phases, starting fresh")
        else:
            # Check for phases where JSONL succeeded but state wasn't saved (the hang bug)
            agent_map = {"build": "builder", "plan": "planner", "research": "researcher",
                         "test": "tester", "validate": "validator", "review": "reviewer",
                         "document": "documenter"}
            for phase_name, agent_name in agent_map.items():
                if phase_name not in phase_results:
                    if recover_phase_from_jsonl(adw_id, phase_name, agent_name, state):
                        print(f"  Recovered {phase_name} phase from JSONL output (completed but state was not saved)")
                        logger.info(f"Resume: recovered {phase_name} from JSONL")

            # Reload state after potential recovery
            state = TravisState.load(adw_id)
            resume_from_phase = get_resume_phase(state)

            if resume_from_phase is None:
                print("All phases already completed!")
                print_final_summary(state)
                sys.exit(0)

            # Print what we're skipping
            for phase in PHASE_ORDER:
                if phase == resume_from_phase:
                    break
                result = state.get_phase_result(phase)
                if result and result.get("success"):
                    print(f"  Skipping {phase}: already completed ✓")
                    logger.info(f"Resume: skipping completed phase {phase}")

            print(f"\n  Resuming from: {resume_from_phase}\n")
            logger.info(f"Resume: resuming from {resume_from_phase}")

    def should_skip_phase(phase: str) -> bool:
        """Check if a phase should be skipped due to resume mode."""
        if not resume_mode or resume_from_phase is None:
            return False
        try:
            return PHASE_ORDER.index(phase) < PHASE_ORDER.index(resume_from_phase)
        except ValueError:
            return False

    all_success = True
    failed_phase = None

    # Phase 0: Research (optional, unless --skip-research)
    if not skip_research and not should_skip_phase("research"):
        success, error = run_phase(
            "travis_research.py",
            [input_arg, adw_id, "--model", model],
            "Phase 0: Research",
            required=False  # Non-blocking - workflow continues even if research fails
        )
        if not success:
            logger.warning(f"Research phase failed: {error}")
            # Don't set all_success to False - research is optional

    # Phase 1: Plan
    if not should_skip_phase("plan"):
        success, error = run_phase(
            "travis_plan.py",
            [input_arg, adw_id, "--plan-type", plan_type, "--model", model],
            "Phase 1: Planning",
            required=True
        )
        if not success:
            logger.error(f"Planning phase failed: {error}")
            all_success = False
            failed_phase = "Plan"

    # Phase 2: Build
    if all_success and not should_skip_phase("build"):
        success, error = run_phase(
            "travis_build.py",
            [adw_id],
            "Phase 2: Implementation",
            required=True
        )
        if not success:
            logger.error(f"Build phase failed: {error}")
            all_success = False
            failed_phase = "Build"

    # Phase 3: Validate (unless --skip-validate)
    if all_success and not skip_validate and not should_skip_phase("validate"):
        validate_args = [adw_id, "--max-retries", str(max_validation_retries)]
        if validate_warnings_only:
            validate_args.append("--warnings-only")
        success, error = run_phase(
            "travis_validate.py",
            validate_args,
            "Phase 3: Validation",
            required=False  # Allow workflow to continue even if validation fails
        )
        if not success:
            logger.warning(f"Validation phase failed: {error}")
            # Track failure but don't stop workflow
            if not failed_phase:
                failed_phase = "Validate"

    # Phase 4: Test
    if all_success and not should_skip_phase("test"):
        test_args = [adw_id, "--max-retries", str(max_test_retries)]
        if mcp_config:
            test_args.extend(["--mcp-config", mcp_config])
        success, error = run_phase(
            "travis_test.py",
            test_args,
            "Phase 4: Testing",
            required=False  # Allow workflow to continue even if tests fail
        )
        if not success:
            logger.warning(f"Test phase failed: {error}")
            # Track failure but don't stop workflow
            if not failed_phase:
                failed_phase = "Test"

    # Phase 5: Review (optional) - run even if tests failed
    if not skip_review and not should_skip_phase("review"):
        success, error = run_phase(
            "travis_review.py",
            [adw_id],
            "Phase 5: Review",
            required=False  # Don't stop workflow if review fails
        )
        if not success:
            logger.warning(f"Review phase failed: {error}")
            # Don't set all_success to False - review is informational

    # Phase 6: Document (optional) - run even if tests failed
    if not skip_document and not should_skip_phase("document"):
        success, error = run_phase(
            "travis_document.py",
            [adw_id],
            "Phase 6: Documentation",
            required=False  # Don't stop workflow if documentation fails
        )
        if not success:
            logger.warning(f"Documentation phase failed: {error}")
            # Don't set all_success to False - documentation is optional

    # Load final state and print summary
    state = TravisState.load(adw_id)
    print_final_summary(state)

    # Final result
    if all_success:
        logger.info("Travis SDLC completed successfully")
        print(f"{'='*70}")
        print("  ✅ WORKFLOW COMPLETED SUCCESSFULLY")
        print(f"{'='*70}\n")
        sys.exit(0)
    elif failed_phase == "Validate":
        # Validation failed but workflow continued - exit with warning
        logger.warning("Travis SDLC completed with validation failures")
        print(f"{'='*70}")
        print("  ⚠️  WORKFLOW COMPLETED WITH VALIDATION FAILURES")
        print(f"{'='*70}\n")
        sys.exit(0)  # Exit success so workflow continues
    elif failed_phase == "Test":
        # Tests failed but workflow continued - exit with warning
        logger.warning("Travis SDLC completed with test failures")
        print(f"{'='*70}")
        print("  ⚠️  WORKFLOW COMPLETED WITH TEST FAILURES")
        print(f"{'='*70}\n")
        sys.exit(0)  # Exit success so workflow continues
    else:
        logger.error(f"Travis SDLC failed at {failed_phase} phase")
        print(f"{'='*70}")
        print(f"  ❌ WORKFLOW FAILED AT {failed_phase.upper()} PHASE")
        print(f"{'='*70}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
