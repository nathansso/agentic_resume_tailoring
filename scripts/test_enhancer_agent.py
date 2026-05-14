import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from agents.enhancer import ProjectEnhancerAgent

logging.basicConfig(level=logging.INFO)

def test_enhancer():
    print("--- Testing Project Enhancer ---")
    agent = ProjectEnhancerAgent()
    try:
        agent.enhance_all_projects()
        print("Enhancement complete.")
    except Exception as e:
        print(f"Enhancement failed: {e}")

if __name__ == "__main__":
    test_enhancer()
