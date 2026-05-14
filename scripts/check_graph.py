"""Quick check of knowledge graph edges."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import init_db
init_db()
from knowledge_graph.builder import SkillGraphBuilder

b = SkillGraphBuilder()
G = b.build_graph()

print("EDGES:")
for u, v, d in G.edges(data=True):
    u_name = G.nodes[u].get("name", u)
    v_name = G.nodes[v].get("name", v)
    print(f"  {u_name} --{d.get('relation', '?')}--> {v_name}")

print()
print("SKILLS with project/experience connections:")
for n, d in G.nodes(data=True):
    if d.get("type") == "Skill":
        node_id = f"Skill:{d['name']}"
        preds = list(G.predecessors(node_id))
        if preds:
            sources = [G.nodes[p].get("name", "?") for p in preds]
            print(f"  {d['name']} <- {sources}")
