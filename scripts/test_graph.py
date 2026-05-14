import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from knowledge_graph.builder import SkillGraphBuilder

logging.basicConfig(level=logging.INFO)

def test_graph():
    print("--- Testing Knowledge Graph ---")
    builder = SkillGraphBuilder()
    graph = builder.build_graph()
    
    print(f"Graph Nodes: {graph.number_of_nodes()}")
    print(f"Graph Edges: {graph.number_of_edges()}")
    
    # Query test
    print("Projects using 'Python' (heuristic check):")
    # Need to know actual skills in DB, guessing Python is there due to resume
    print(builder.get_projects_using_skill("Python"))

if __name__ == "__main__":
    test_graph()
