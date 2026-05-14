"""Quick script to visualize the knowledge graph."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import init_db
init_db()

from knowledge_graph.builder import SkillGraphBuilder
builder = SkillGraphBuilder()
G = builder.build_graph()

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.patches import Patch

# Color nodes by type
color_map = []
for node in G.nodes(data=True):
    t = node[1].get("type", "")
    if t == "Skill":
        color_map.append("#4CAF50")
    elif t == "Project":
        color_map.append("#2196F3")
    elif t == "Experience":
        color_map.append("#FF9800")
    else:
        color_map.append("#9E9E9E")

plt.figure(figsize=(16, 10))
pos = nx.spring_layout(G, k=1.5, seed=42)

nx.draw_networkx_nodes(G, pos, node_color=color_map, node_size=800, alpha=0.9)
nx.draw_networkx_edges(G, pos, edge_color="#BDBDBD", arrows=True, arrowsize=12, alpha=0.6)

# Shortened labels
labels = {}
for n, d in G.nodes(data=True):
    name = d.get("name", n)
    if len(name) > 25:
        name = name[:22] + "..."
    labels[n] = name
nx.draw_networkx_labels(G, pos, labels, font_size=7, font_weight="bold")

# Edge labels
edge_labels = {(u, v): d.get("relation", "") for u, v, d in G.edges(data=True)}
nx.draw_networkx_edge_labels(G, pos, edge_labels, font_size=6, alpha=0.7)

# Legend
n_skills = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "Skill")
n_projects = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "Project")
n_exps = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "Experience")
legend = [
    Patch(color="#4CAF50", label=f"Skills ({n_skills})"),
    Patch(color="#2196F3", label=f"Projects ({n_projects})"),
    Patch(color="#FF9800", label=f"Experiences ({n_exps})"),
]
plt.legend(handles=legend, loc="upper left", fontsize=10)
plt.title("ART Knowledge Graph", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig("knowledge_graph.png", dpi=150)

print(f"Saved knowledge_graph.png")
print(f"Nodes: {G.number_of_nodes()}, Edges: {G.number_of_edges()}")
print()
for n, d in G.nodes(data=True):
    print(f"  [{d.get('type', '?')}] {d.get('name', n)}")
print()
for u, v, d in G.edges(data=True):
    u_name = G.nodes[u].get("name", u)
    v_name = G.nodes[v].get("name", v)
    print(f"  {u_name} --{d.get('relation', '?')}--> {v_name}")
