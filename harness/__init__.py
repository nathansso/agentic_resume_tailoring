"""The model-free tool surface a coding agent drives (epic #207, docs/harness.md).

Nothing in this package may import a generative model client (`llm`,
`langchain_*`, `anthropic`, `openai`); see root CLAUDE.md § Architecture
invariants.
"""

# Recorded in every tailoring-tree node's provenance (#196).
ART_VERSION = "0.1.0"
