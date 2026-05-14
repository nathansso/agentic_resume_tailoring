"""Quick debug script to check what the GitHub ingestor fetches."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingestion.github import GitHubIngestor

ingestor = GitHubIngestor("nathansso")
repos = ingestor.ingest()

print(f"Total repos found: {len(repos)}\n")
for r in repos:
    name = r["name"]
    deps = r.get("dependencies", {})
    langs = r["languages"]
    # Only print repos that have detected imports or dependencies
    if not deps and not r.get("readme"):
        print(f"  {name}: languages={langs}, no deep scan data")
        continue

    print(f"=== {name} ===")
    print(f"  Languages: {langs}")
    print(f"  Description: {r['description']}")
    for df, dc in deps.items():
        print(f"  {df}:")
        for line in dc[:500].split("\n"):
            print(f"    {line}")
    readme = r.get("readme") or ""
    if readme:
        print(f"  README ({len(readme)} chars):")
        for line in readme[:300].split("\n"):
            print(f"    {line}")
    print()
