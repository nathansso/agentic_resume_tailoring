"""Repository dossiers for `artie ingest` (#398): what the agent reads to write about a repo.

A dossier is #397's `blame-20k`, built from a clone with git and nothing else: README,
metadata, the filtered tree, the user's authorship, a symbol map ranked by PageRank seeded with
the user's files and tagged with blame ownership, and "built by other authors", fitted to a
20K-token cap. Every line cites a path, a line span or a commit. See `build.py` for the
sections, `docs/research/repo-map.md` § 6 for the spec.

Model-free and offline: fetching the clone is `harness/github.py`'s job.
"""

from harness.dossier.build import (BUDGET_TOKENS, BUILDER_VERSION, CHARS_PER_TOKEN, SHARES,
                                   Dossier, build, fit)
from harness.dossier.identity import Identity, Seeds, local_git_identity

__all__ = ["build", "fit", "Dossier", "Seeds", "Identity", "local_git_identity",
           "BUILDER_VERSION", "BUDGET_TOKENS", "CHARS_PER_TOKEN", "SHARES"]
