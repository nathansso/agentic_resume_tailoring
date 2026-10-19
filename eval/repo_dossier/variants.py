"""Dossier variants at matched token budgets: finishing the #397 spike.

#397's ladder grows its input rung by rung, so rungs differ in size as well as content. To
compare methods fairly, every variant here is fitted to the same total budget (5K, 10K or 20K
tokens, estimated at 2.3 characters a token, the rate #397's writer calls measured on its
dossiers: 2.2 to 2.7, median 2.4) by one rule, `fit`: each section gets a share of the
budget, a section that needs less gives the rest back to the others, and a section that needs
more is cut at a line boundary with a marker saying so.

Variants:

| id | symbol map | attribution layered on #397's per-file authorship |
|---|---|---|
| `5r-5k`, `5r-10k`, `5r-20k` | regex (#397's pick) | none |
| `ts-10k` | tree-sitter with each grammar's `tags.scm` (Aider's method) | none |
| `ast-10k` | Python `ast`, regex for other languages | none |
| `ctags-10k` | Universal Ctags | none |
| `pyg-10k` | Pygments lexers | none |
| `blame-10k` | regex, each definition tagged with its blame ownership | + "built by others" |
| `commits-10k` | regex | per-file commit subjects, Conventional Commits types |
| `prs-10k` | regex | + the user's merged PRs |
| `own-10k`, `own-20k` | regex, ownership-tagged | all three: by-file subjects, PRs, built by others |

Every section cites its sources: paths, line spans (`L12-40`), commit SHAs and PR numbers. The
header names the commit the dossier was built at.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from eval.repo_dossier import attribution as attr
from eval.repo_dossier.ladder import (CODE_EXT, NOT_SOURCE, Authorship, Budgets, RepoInput,
                                      _ext, _git, authorship, authorship_section, file_as_text,
                                      list_files, metadata_section, readme_section, symlinks,
                                      tree_section)
from eval.repo_dossier.symbols import build_symbol_map_v2, language_of

CHARS_PER_TOKEN = 2.3   # measured: #397's dossiers ran 2.16-2.68 chars per writer input token
MARKUP = {".html", ".css", ".scss"}


@dataclass(frozen=True)
class Variant:
    backend: str                  # the symbol-map backend
    budget: int                   # total tokens
    layers: tuple[str, ...] = ()  # "blame", "others", "by_file", "prs"


VARIANTS: dict[str, Variant] = {
    "5r-5k": Variant("regex", 5_000),
    "5r-10k": Variant("regex", 10_000),
    "5r-20k": Variant("regex", 20_000),
    "ts-10k": Variant("ts-tags", 10_000),
    "ast-10k": Variant("ast", 10_000),
    "ctags-10k": Variant("ctags", 10_000),
    "pyg-10k": Variant("pygments", 10_000),
    "blame-10k": Variant("regex", 10_000, ("blame", "others")),
    "commits-10k": Variant("regex", 10_000, ("by_file",)),
    "prs-10k": Variant("regex", 10_000, ("prs",)),
    "own-10k": Variant("regex", 10_000, ("blame", "others", "by_file", "prs")),
    "own-20k": Variant("regex", 20_000, ("blame", "others", "by_file", "prs")),
}
VARIANT_NAMES = {
    "5r-5k": "5r at 5K", "5r-10k": "5r at 10K", "5r-20k": "5r at 20K",
    "ts-10k": "tree-sitter tags (Aider)", "ast-10k": "Python ast + regex", "ctags-10k": "Universal Ctags",
    "pyg-10k": "Pygments", "blame-10k": "5r + blame ownership", "commits-10k": "5r + commit subjects by file",
    "prs-10k": "5r + merged PRs", "own-10k": "5r + all attribution", "own-20k": "5r + all attribution at 20K",
}
BASELINE = "5r-10k"

# The share of the budget each section asks for (normalised over the sections present).
SHARES = {"readme": 0.30, "metadata": 0.12, "tree": 0.10, "authorship": 0.23, "symbols": 0.25,
          "prs": 0.14, "others": 0.06}


@dataclass
class RepoContext:
    """Everything the variants share for one repo, computed once (blame lazily)."""
    repo: RepoInput
    files: list[str]
    auth: Authorship
    sources_lang: dict[str, str]      # files with a regex/tree-sitter/ast language
    sources_all: dict[str, str]       # every source file (for ctags and Pygments)
    head: str
    prs: list[dict] | None
    sections: dict[str, str] = field(default_factory=dict)
    blame: attr.BlameIndex | None = None
    by_file: dict | None = None
    timings: dict[str, float] = field(default_factory=dict)


def make_context(repo: RepoInput, prs: list[dict] | None = None) -> RepoContext:
    t0 = time.perf_counter()
    path = Path(repo.path)
    files = list_files(path)
    auth = authorship(path, repo.user_emails)
    big = Budgets(readme_chars=40_000, manifest_chars=3_000, manifests_max=12, tree_lines=600,
                  author_files=120, author_messages=200)
    srcs = {}
    links = symlinks(path)
    for f in files:
        # a symlink's target is tracked too, so its code is mapped once, under the real path
        if f not in links and _ext(f) in CODE_EXT and _ext(f) not in MARKUP and not NOT_SOURCE.search(f):
            srcs[f] = file_as_text(path, f, big, code_only=True)
    ctx = RepoContext(repo, files, auth,
                      {f: c for f, c in srcs.items() if language_of(f)}, srcs,
                      _git(path, "rev-parse", "HEAD").strip(), prs)
    ctx.sections = {
        "readme": readme_section(path, files, big),
        "metadata": metadata_section(path, files, repo.meta, big),
        "tree": tree_section(path, files, big),
        "authorship": authorship_section(auth, repo.user_label, set(files), big),
    }
    ctx.timings["context_s"] = time.perf_counter() - t0
    return ctx


def _multi_author(ctx: RepoContext) -> bool:
    return ctx.auth.authors > 1 and ctx.auth.user_commits > 0


def fit(sections: list[tuple[str, str]], budget_tokens: int,
        chars_per_token: float | None = None) -> tuple[dict[str, str], dict[str, int]]:
    """Fit sections into the budget by their shares (water-filling): a section needing less than
    its share keeps what it needs and the rest is shared among the others; a section needing more
    is cut at a line boundary. Returns the texts and each section's allotted characters."""
    total = int(budget_tokens * (chars_per_token or CHARS_PER_TOKEN) * 0.98)   # 2% for the title and separators
    need = {k: len(t) for k, t in sections}
    alloc: dict[str, int] = {}
    left = dict.fromkeys(need)
    remaining = total
    changed = True
    while left and changed:
        changed = False
        share_sum = sum(SHARES[k] for k in left)
        for k in sorted(left, key=lambda k: (need[k], k)):
            fair = remaining * SHARES[k] / share_sum
            if need[k] <= fair:
                alloc[k] = need[k]
                remaining -= need[k]
                del left[k]
                changed = True
                break
    share_sum = sum(SHARES[k] for k in left) or 1
    for k in left:
        alloc[k] = int(remaining * SHARES[k] / share_sum)
    out = {}
    for k, text in sections:
        out[k] = text if len(text) <= alloc[k] else _cut(text, alloc[k])
    return out, alloc


def _cut(text: str, chars: int) -> str:
    marker = "\n…[section cut to fit the dossier's token budget]"
    room = max(0, chars - len(marker))
    head = text[:room]
    if "\n" in head:
        head = head[:head.rindex("\n")]
    return head + marker


def build_variant(ctx: RepoContext, vid: str) -> tuple[str, dict]:
    """The dossier text for one variant, and its stats (section sizes, time, symbol backend)."""
    v = VARIANTS[vid]
    t0 = time.perf_counter()
    path = Path(ctx.repo.path)
    multi = _multi_author(ctx)
    sections: list[tuple[str, str]] = [("readme", ctx.sections["readme"]),
                                       ("metadata", ctx.sections["metadata"]),
                                       ("tree", ctx.sections["tree"])]
    if "by_file" in v.layers:
        if ctx.by_file is None:
            ctx.by_file = attr.user_commits_by_file(path, ctx.repo.user_emails)
        sections.append(("authorship", attr.by_file_section(ctx.auth, ctx.by_file, ctx.repo.user_label,
                                                            set(ctx.files))))
    else:
        sections.append(("authorship", ctx.sections["authorship"]))
    if "prs" in v.layers:
        sections.append(("prs", attr.prs_section(ctx.prs or [], ctx.repo.user_label)
                         if ctx.prs is not None else
                         f"## Merged pull requests ({ctx.repo.user_label})\n(not fetched)"))
    use_blame = ("blame" in v.layers or "others" in v.layers) and multi
    if use_blame and ctx.blame is None:
        ctx.blame = attr.BlameIndex(path, {e.lower() for e in ctx.repo.user_emails})
    sources = ctx.sources_all if v.backend in ("ctags", "pygments") else ctx.sources_lang
    weights = {p: w for p, w in ctx.auth.weights().items() if p in sources}

    # The symbol map is sized to its allotment: first measure its demand at a generous budget.
    sym_budget = int(v.budget * 0.45)
    annotate = ctx.blame.annotate if (use_blame and "blame" in v.layers) else None
    header = ("## Symbol map (definitions ranked by PageRank seeded with the user's files; "
              f"line spans{'; ownership by git blame' if annotate else ''})\n")
    smap = build_symbol_map_v2(sources, weights, sym_budget, v.backend, path, annotate,
                               chars_per_token=CHARS_PER_TOKEN)
    sections.append(("symbols", header + (smap.text or "(no definitions found)")))
    if "others" in v.layers:
        sections.append(("others", attr.others_section(smap.ranked, ctx.blame) if use_blame else
                         "## Built by other authors\n(single-author repository)"))
    fitted, alloc = fit(sections, v.budget)
    if len(dict(sections)["symbols"]) > alloc["symbols"]:
        smap = build_symbol_map_v2(sources, weights, int((alloc["symbols"] - len(header)) / CHARS_PER_TOKEN),
                                   v.backend, path, annotate, chars_per_token=CHARS_PER_TOKEN)
        fitted["symbols"] = header + (smap.text or "(no definitions found)")
    title = f"# Repository dossier: {ctx.repo.name} at commit {ctx.head[:12]}"
    text = "\n\n".join([title] + [fitted[k] for k, _ in sections])
    stats = {"chars": len(text), "est_tokens": round(len(text) / CHARS_PER_TOKEN),
             "section_chars": {k: len(fitted[k]) for k, _ in sections},
             "cut": sorted(k for k, t in sections if len(t) > alloc[k]),
             "symbol_backend": smap.backend, "symbol_defs": smap.definitions,
             "symbol_rendered": smap.rendered, "build_s": round(time.perf_counter() - t0, 3),
             "blamed_files": (len([x for x in ctx.blame.files.values() if x is not None])
                              if ctx.blame else 0)}
    return text, stats


def judge_prs_section(prs: list[dict] | None, user_label: str) -> str:
    """For the judge: every merged PR of the user's, with full bodies and file lists."""
    if not prs:
        return ""
    return attr.prs_section(prs, user_label, max_prs=500, body_chars=4_000, files_per_pr=200)
