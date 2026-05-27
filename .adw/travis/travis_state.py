#!/usr/bin/env -S uv run
# /// script
# dependencies = ["python-dotenv", "pydantic"]
# ///

"""Travis workflow state management module.

Simple state management for Travis workflows. Stores minimal state data
in agents/{adw_id}/travis_state.json to track phase execution and results.
"""

import json
import os
import sys
from typing import Dict, Any, Optional
from pathlib import Path


class TravisState:
    """Manages state for Travis workflow execution.

    State is stored in: agents/{adw_id}/travis_state.json

    State structure:
    {
        "adw_id": str,
        "research_file": Optional[str],
        "plan_file": Optional[str],
        "spec_file": Optional[str],
        "phase_results": {
            "research": Optional[dict],
            "plan": Optional[dict],
            "build": Optional[dict],
            "test": Optional[dict],
            "review": Optional[dict],
            "document": Optional[dict]
        }
    }
    """

    def __init__(self, adw_id: str):
        """Initialize state manager.

        Args:
            adw_id: The ADW workflow ID
        """
        self.adw_id = adw_id
        self.data: Dict[str, Any] = {
            "adw_id": adw_id,
            "research_file": None,
            "plan_file": None,
            "spec_file": None,
            "phase_results": {}
        }
        self._state_file = self._get_state_file_path()

    def _get_state_file_path(self) -> str:
        """Get the path to the state file.

        Returns:
            Absolute path to travis_state.json
        """
        # Get project root (3 levels up from adws/travis/)
        project_root = Path(__file__).parent.parent.parent
        state_dir = project_root / "agents" / self.adw_id
        state_dir.mkdir(parents=True, exist_ok=True)
        return str(state_dir / "travis_state.json")

    @classmethod
    def load(cls, adw_id: str) -> "TravisState":
        """Load state from file or create new state.

        Args:
            adw_id: The ADW workflow ID

        Returns:
            TravisState instance
        """
        state = cls(adw_id)

        if os.path.exists(state._state_file):
            try:
                with open(state._state_file, "r") as f:
                    state.data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load state file: {e}", file=sys.stderr)
                # Keep default state

        return state

    def save(self) -> None:
        """Save state to file."""
        try:
            with open(self._state_file, "w") as f:
                json.dump(self.data, f, indent=2)
        except IOError as e:
            print(f"Error: Could not save state file: {e}", file=sys.stderr)
            sys.exit(1)

    def update(self, **kwargs) -> None:
        """Update state fields.

        Args:
            **kwargs: Fields to update (plan_file, spec_file, etc.)
        """
        for key, value in kwargs.items():
            if key in self.data:
                self.data[key] = value

    def set_phase_result(self, phase: str, result: Dict[str, Any]) -> None:
        """Set result for a phase.

        Args:
            phase: Phase name (plan, build, test, review, document)
            result: Phase result dictionary
        """
        self.data["phase_results"][phase] = result

    def get_phase_result(self, phase: str) -> Optional[Dict[str, Any]]:
        """Get result for a phase.

        Args:
            phase: Phase name

        Returns:
            Phase result dict or None if not found
        """
        return self.data["phase_results"].get(phase)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value.

        Args:
            key: State key
            default: Default value if key not found

        Returns:
            State value or default
        """
        return self.data.get(key, default)

    def has_phase_completed(self, phase: str) -> bool:
        """Check if a phase has completed.

        Args:
            phase: Phase name

        Returns:
            True if phase result exists
        """
        return phase in self.data["phase_results"]


def main():
    """Test state management."""
    if len(sys.argv) < 2:
        print("Usage: uv run travis_state.py <adw-id>")
        sys.exit(1)

    adw_id = sys.argv[1]

    # Test state operations
    state = TravisState.load(adw_id)
    print(f"Loaded state for {adw_id}")
    print(json.dumps(state.data, indent=2))

    # Update some fields
    state.update(plan_file="specs/test.md")
    state.set_phase_result("plan", {"success": True, "file": "specs/test.md"})
    state.save()
    print("\nUpdated and saved state")

    # Load again to verify
    state2 = TravisState.load(adw_id)
    print("\nReloaded state:")
    print(json.dumps(state2.data, indent=2))


if __name__ == "__main__":
    main()
