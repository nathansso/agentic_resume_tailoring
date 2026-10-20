"""Build one repository's dossier: #397's `blame-20k` (#398).

The sections, in order, each citing its sources (a path, a line span, a short SHA):

1. the header, naming the commit the dossier was built at;
2. the README (a symlinked README reads its target);
3. metadata: GitHub's description and topics, languages by committed bytes, first and last
   commit, dependency manifests, detected imports;
4. the filtered file tree with committed sizes;
5. the user's authorship: commits, share, changed files with +/-, commit messages;
6. the symbol map, ranked by PageRank seeded with the user's files, every definition tagged
   with its blame ownership and introducing commit;
7. "built by other authors", tests left out.

Fitted to `BUDGET_TOKENS` at `CHARS_PER_TOKEN` by the shares in `SHARES` (`fit`): a section
that needs less than its share gives the rest back, one that needs more is cut at a line with
a marker. Blame is skipped in a single-author repository.

Deterministic: the same commit and inputs give the same bytes on every OS and hash seed. The
builder reads git only; it never reaches the network and never calls a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from harness.dossier import blame as blame_mod
from harness.dossier import sections as sec
from harness.dossier import symbols as sym
from harness.dossier.gitread import Snapshot, file_as_text, is_source, snapshot
from harness.dossier.identity import Identity, Seeds, resolve

# Bump when the dossier's text changes for the same commit, so `artie ingest` rebuilds.
BUILDER_VERSION = "blame-20k/1"
BUDGET_TOKENS = 20_000
CHARS_PER_TOKEN = 2.3      # measured on #397's dossiers: 2.2-2.7 characters a writer token
SHARES = {"readme": 0.30, "metadata": 0.12, "tree": 0.10, "authorship": 0.23, "symbols": 0.25,
          "others": 0.06}
SYMBOL_HEADER = ("## Symbol map (definitions ranked by PageRank seeded with the user's files; "
                 "line spans{blame})\n")
CUT_MARKER = "\n…[section cut to fit the dossier's token budget]"


@dataclass
class Dossier:
    name: str
    commit: str
    text: str
    stats: Dict = field(default_factory=dict)


def fit(sections: List[Tuple[str, str]], budget_tokens: int = BUDGET_TOKENS,
        chars_per_token: float = CHARS_PER_TOKEN,
        shares: Optional[Mapping[str, float]] = None) -> Tuple[Dict[str, str], Dict[str, int]]:
    """Fit sections into the budget by their shares (water-filling): a section needing less than
    its share keeps what it needs and the rest is shared among the others; a section needing
    more is cut at a line boundary. Returns the texts and each section's allotted characters."""
    total = int(budget_tokens * chars_per_token * 0.98)   # 2% for the title and separators
    shares = shares or SHARES
    need = {k: len(t) for k, t in sections}
    alloc: Dict[str, int] = {}
    left = dict.fromkeys(need)
    remaining = total
    changed = True
    while left and changed:
        changed = False
        share_sum = sum(shares[k] for k in left)
        for k in sorted(left, key=lambda k: (need[k], k)):
            fair = remaining * shares[k] / share_sum
            if need[k] <= fair:
                alloc[k] = need[k]
                remaining -= need[k]
                del left[k]
                changed = True
                break
    share_sum = sum(shares[k] for k in left) or 1
    for k in left:
        alloc[k] = int(remaining * shares[k] / share_sum)
    out = {}
    for k, text in sections:
        out[k] = text if len(text) <= alloc[k] else cut(text, alloc[k])
    return out, alloc


def cut(text: str, chars: int) -> str:
    room = max(0, chars - len(CUT_MARKER))
    head = text[:room]
    if "\n" in head:
        head = head[:head.rindex("\n")]
    return head + CUT_MARKER


def _sources(snap: Snapshot, weights: Mapping[str, float]) -> Dict[str, str]:
    """The files the symbol map reads (at most `MAX_FILES`, the user's first), as code text.
    A symlink's target is tracked too, so its code is mapped once, under the real path."""
    paths = [f for f in snap.files
             if f not in snap.links and is_source(f) and sym.language_of(f)]
    chosen = sym.select_sources(paths, weights)
    snap.prefetch(chosen)
    out = {p: file_as_text(snap, p, code_only=True) for p in chosen}
    return out


def build(repo: Path, name: str, seeds: Seeds, *, rev: str = "HEAD",
          meta: Optional[Mapping] = None, user_label: Optional[str] = None,
          budget_tokens: int = BUDGET_TOKENS) -> Dossier:
    """The `blame-20k` dossier for the repository at `repo` (a clone, bare or not) at `rev`.
    `seeds` say who the user is; `meta` is GitHub's repository record, if fetched."""
    t0 = time.perf_counter()
    snap = snapshot(Path(repo), rev)
    who: Identity = resolve(snap, seeds)
    label = user_label or (f"the user, GitHub {seeds.login}" if seeds.login else "the user")
    lim = sec.Limits()
    auth = sec.authorship(snap, who)
    sections: List[Tuple[str, str]] = [
        ("readme", sec.readme_section(snap, lim)),
        ("metadata", sec.metadata_section(snap, meta, lim)),
        ("tree", sec.tree_section(snap, lim)),
        ("authorship", sec.authorship_section(auth, label, set(snap.files), lim)),
    ]

    sources = _sources(snap, auth.weights())
    weights = {p: w for p, w in auth.weights().items() if p in sources}
    ranked = sym.rank_sources(sources, weights)
    use_blame = auth.multi_author
    index = None
    if use_blame:
        index = blame_mod.BlameIndex(snap, who)
        index.prefetch(p for p, _ in ranked.keys)
    annotate = index.annotate if index else None
    header = SYMBOL_HEADER.format(blame="; ownership by git blame" if annotate else "")

    # The map is first sized generously, then, if the fit cuts it, re-rendered to its
    # allotment (#397's `build_variant`), so it ends on a whole definition.
    gen_tokens = int(budget_tokens * max(0.45, SHARES["symbols"] + 0.05))
    smap = sym.render(ranked, gen_tokens * CHARS_PER_TOKEN, annotate)
    sections.append(("symbols", header + (smap.text or "(no definitions found)")))
    if index is not None:
        others = blame_mod.others_section(smap.ranked, index)
    elif auth.user_commits:
        others = "## Built by other authors\n(single-author repository: blame skipped)"
    else:
        others = "## Built by other authors\n(no commits by the user found: blame skipped)"
    sections.append(("others", others))
    fitted, alloc = fit(sections, budget_tokens)
    if len(dict(sections)["symbols"]) > alloc["symbols"]:
        tokens = int((alloc["symbols"] - len(header)) / CHARS_PER_TOKEN)
        smap = sym.render(ranked, tokens * CHARS_PER_TOKEN, annotate)
        fitted["symbols"] = header + (smap.text or "(no definitions found)")
    title = f"# Repository dossier: {name} at commit {snap.commit[:12]}"
    text = "\n\n".join([title] + [fitted[k] for k, _ in sections])
    stats = {
        "chars": len(text), "est_tokens": round(len(text) / CHARS_PER_TOKEN),
        "section_chars": {k: len(fitted[k]) for k, _ in sections},
        "cut": sorted(k for k, t in sections if len(t) > alloc[k]),
        "files": len(snap.files), "source_files": len(sources),
        "symbol_defs": smap.definitions, "symbol_rendered": smap.rendered,
        "user_commits": auth.user_commits, "total_commits": auth.total_commits,
        "authors": auth.authors, "blamed_files": (
            len([v for v in index.files.values() if v is not None]) if index else 0),
        "build_s": round(time.perf_counter() - t0, 3),
    }
    return Dossier(name, snap.commit, text, stats)


__all__ = ["build", "fit", "cut", "Dossier", "BUILDER_VERSION", "BUDGET_TOKENS",
           "CHARS_PER_TOKEN", "SHARES"]
