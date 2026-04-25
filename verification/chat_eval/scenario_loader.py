"""
Scenario loader and DB seed helpers for the chat eval harness.
Scenarios are JSON files under verification/chat_eval/scenarios/.
"""
import json
from pathlib import Path
from typing import List, Optional

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def load_scenario(scenario_id: str) -> dict:
    """Load a scenario by ID from the scenarios directory. Raises FileNotFoundError if missing."""
    path = SCENARIOS_DIR / f"{scenario_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Scenario not found: {scenario_id} (expected {path})")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_all_scenarios() -> List[dict]:
    """Load all .json scenario files from the scenarios directory."""
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                scenarios.append(json.load(fh))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to load scenario %s: %s", path, exc)
    return scenarios


def seed_profile(profile_fixture: dict) -> object:
    """Create a profile for eval runs using the same pattern as test_smoke_formal.py.

    Returns the created User object. Respects whatever engine is active (real or patched).
    """
    from database.user_utils import create_profile
    user = create_profile(
        name=profile_fixture.get("name", "Eval User"),
        email=profile_fixture.get("email", "eval@test.local"),
        github_username=profile_fixture.get("github_username") or None,
    )
    return user


def seed_scenario_db(scenario: dict) -> Optional[object]:
    """Set up the DB state required by a scenario. Returns the seeded User or None."""
    fixture = scenario.get("profile_fixture")
    if not fixture:
        return None
    user = seed_profile(fixture)

    # Seed initial_chat_history into agent history if caller manages agent separately.
    # (The runner handles this; we just return the user here.)
    return user
