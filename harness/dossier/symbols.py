"""The symbol map: regex definitions ranked by PageRank seeded with the user's files (#398).

Ported from #397's eval (`eval/repo_dossier/symbols.py`, the `regex` backend of
`build_symbol_map_v2`), which no other backend beat (tree-sitter, `ast`, Ctags and Pygments were
all within noise). Files are nodes; an edge runs from a file that references a name to each file
that defines it. Personalised PageRank, seeded with the user's changed files weighted by the
lines they changed, ranks the files; each file's rank is shared over the definitions it points
at, and the best definitions are rendered, one line each, `L<start>-<end>: <signature>`, to a
character budget.

Model-free and dependency-free. The PageRank is a plain power iteration (not
`networkx.pagerank`, which needs SciPy, which art-mcp doesn't ship), and everything that feeds
the ranking is sorted and its scores rounded, so the order is the same on every OS and hash seed.
Bounded on large repos: at most `MAX_FILES` source files (the user's first), references counted
per file, and no edges for a name with more than `MAX_DEFINERS` definers.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Tuple

MAX_FILES = 3_000
MAX_DEFINERS = 20

_EXT_LANG = {
    ".py": "python", ".ipynb": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go",
}
_RX_DEF = {
    "python": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)|^\s*class\s+(\w+)"),
    "javascript": re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"
        r"|^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)"
        r"|^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"
        r"|^\s*(?:export\s+)?(?:interface|type|enum)\s+(\w+)"),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)"),
}
_RX_DEF["typescript"] = _RX_DEF["tsx"] = _RX_DEF["javascript"]
_WORD = re.compile(r"\b[A-Za-z_]\w{2,}\b")


def language_of(path: str) -> Optional[str]:
    low = path.lower()
    for e, lang in _EXT_LANG.items():
        if low.endswith(e):
            return lang
    return None


def regex_spans(lang: str, code: str) -> Tuple[List[Tuple[str, int, int]], Counter]:
    """Definitions as (name, start line, -1), 0-based, and a count of the words referenced."""
    rx = _RX_DEF.get(lang)
    if rx is None:
        return [], Counter()
    defs = []
    for i, line in enumerate(code.splitlines()):
        m = rx.match(line)
        if m:
            name = next((g for g in m.groups() if g), None)
            if name:
                defs.append((name, i, -1))
    return defs, Counter(m.group(0) for m in _WORD.finditer(code))


def close_spans(defs: List[tuple], lines: List[str], indented: bool = False) -> List[tuple]:
    """Fill unknown ends. In an indentation language a definition runs to the line before the
    next definition at the same or a shallower indent; otherwise to the line before the next
    definition. Trailing blank lines are left out."""
    defs = sorted(defs, key=lambda d: (d[1], d[0]))

    def indent(i):
        text = lines[i] if 0 <= i < len(lines) else ""
        return len(text) - len(text.lstrip())

    out = []
    for i, (name, start, end) in enumerate(defs):
        if end < 0:
            later = [d[1] for d in defs[i + 1:] if d[1] > start]
            if indented:
                later = [ln for ln in later if indent(ln) <= indent(start)]
            end = max(start, (later[0] if later else len(lines)) - 1)
            while end > start and not lines[end].strip():
                end -= 1
        out.append((name, start, end))
    return out


def posix_dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def select_sources(paths: List[str], user_weights: Mapping[str, float],
                   max_files: int = MAX_FILES) -> List[str]:
    """On a repo with more source files than `max_files`, the files the map is built from: the
    user's changed files (most lines first), then the other files in their directories, then
    the rest by path, up to the cap. Below the cap, every file. Sorted."""
    if len(paths) <= max_files:
        return sorted(paths)
    mine = sorted((p for p in paths if user_weights.get(p, 0) > 0),
                  key=lambda p: (-user_weights[p], p))
    mine_set = set(mine)
    dirs = {posix_dir(p) for p in mine}
    near = sorted(p for p in paths if posix_dir(p) in dirs and p not in mine_set)
    rest = sorted(p for p in paths if posix_dir(p) not in dirs)
    return sorted((mine + near + rest)[:max_files])


def _total(values) -> float:
    """A left-to-right float sum (what `sum` does, named so the order is deliberate)."""
    t = 0.0
    for v in values:
        t += v
    return t


def pagerank(nodes: List[str], links: Mapping[str, Mapping[str, float]],
             personalization: Optional[Mapping[str, float]], alpha: float = 0.85,
             max_iter: int = 100, tol: float = 1.0e-6,
             out_weights: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    """Weighted personalised PageRank, a plain power iteration with networkx's semantics
    (parallel edges summed, dangling mass sent by the personalisation vector). Nodes and edges
    are visited in a fixed order, so the floats are the same on every platform. `out_weights`
    overrides each node's out-weight (by default the sum of its links)."""
    n = len(nodes)
    if n == 0:
        return {}
    out_w = {u: (out_weights[u] if out_weights is not None and u in out_weights
                 else _total(links[u].values()) if u in links else 0.0) for u in nodes}
    if personalization:
        total = sum(personalization.get(v, 0.0) for v in nodes)
        p = {v: personalization.get(v, 0.0) / total for v in nodes}
    else:
        p = {v: 1.0 / n for v in nodes}
    x = {v: 1.0 / n for v in nodes}
    dangling = [v for v in nodes if out_w[v] == 0]
    for _ in range(max_iter):
        last = x
        x = dict.fromkeys(nodes, 0.0)
        dsum = alpha * sum(last[v] for v in dangling)
        for u in nodes:
            if out_w[u]:
                share = alpha * last[u] / out_w[u]
                for v, w in links[u].items():
                    x[v] += share * w
        for v in nodes:
            x[v] += dsum * p[v] + (1.0 - alpha) * p[v]
        if sum(abs(x[v] - last[v]) for v in nodes) < n * tol:
            return x
    raise RuntimeError("pagerank did not converge")


def rank(paths: List[str], defines: Mapping[str, set], references: Mapping[str, Counter],
         def_keys, user_weights: Mapping[str, float]) -> List[Tuple[str, str]]:
    """(path, name) definition keys, best first."""
    user_weights = {p: w for p, w in user_weights.items() if p in set(paths) and w > 0}
    # edges[src] = [(dst, name, weight)] in insertion order; links[src][dst] = summed weight
    edges: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
    for name in sorted(set(defines) & set(references)):
        definers = sorted(defines[name])
        if len(definers) > MAX_DEFINERS:
            continue
        mul = 1.0
        if len(name) >= 8 and ("_" in name or re.search(r"[a-z][A-Z]", name)):
            mul *= 10
        if name.startswith("_"):
            mul *= 0.1
        if len(definers) > 5:
            mul *= 0.1
        for referencer, n in sorted(references[name].items()):
            w = mul * math.sqrt(n) * (5 if referencer in user_weights else 1)
            for definer in definers:
                edges[referencer].append((definer, name, w))
    # Each source's edges grouped by target in first-seen order, the order networkx's
    # MultiDiGraph visits them, so sums run in the same order as #397's eval.
    nodes = list(paths)
    grouped: Dict[str, Dict[str, List[Tuple[str, float]]]] = {}
    for u in nodes:
        if u in edges:
            row: Dict[str, List[Tuple[str, float]]] = {}
            for v, name, w in edges[u]:
                row.setdefault(v, []).append((name, w))
            grouped[u] = row
    links = {u: {v: _total(w for _, w in es) for v, es in row.items()} for u, row in grouped.items()}
    out_w = {u: _total(w for es in row.values() for _, w in es) for u, row in grouped.items()}
    total = sum(user_weights.values())
    pers = {node: user_weights.get(node, 0.0) / total for node in nodes} if total else None
    try:
        pr = pagerank(nodes, links, pers, out_weights=out_w)
    except RuntimeError:  # no convergence on a degenerate graph
        pr = {node: 1.0 / max(1, len(nodes)) for node in nodes}

    scores: Dict[Tuple[str, str], float] = defaultdict(float)
    for src in nodes:
        out = [(dst, name, w) for dst, row in grouped.get(src, {}).items() for name, w in row]
        weight = _total(w for _, _, w in out)
        for dst, name, w in out:
            scores[(dst, name)] += pr[src] * w / weight
    for key in def_keys:  # unreferenced definitions still rank, below any referenced one
        scores.setdefault(key, pr.get(key[0], 0.0) * 0.01)
    scores = {k: float(f"{v:.10g}") for k, v in scores.items()}
    return sorted((k for k in scores if k in def_keys), key=lambda k: (-scores[k], k))


@dataclass
class SymbolMap:
    text: str
    files_parsed: int
    definitions: int
    rendered: int
    ranked: List[Tuple[str, str, int, int]] = field(default_factory=list)  # best first


@dataclass
class Ranked:
    """The ranking, computed once and rendered at any budget."""
    keys: List[Tuple[str, str]]
    info: Dict[Tuple[str, str], Tuple[int, int, str]]   # (start, end, signature)
    files_parsed: int

    @property
    def spans(self) -> List[Tuple[str, str, int, int]]:
        return [(p, n, self.info[(p, n)][0], self.info[(p, n)][1]) for p, n in self.keys]


def rank_sources(sources: Mapping[str, str], user_weights: Mapping[str, float]) -> Ranked:
    """Parse `sources` (path -> code, already selected) and rank their definitions."""
    defines: Dict[str, set] = defaultdict(set)
    references: Dict[str, Counter] = defaultdict(Counter)
    info: Dict[Tuple[str, str], Tuple[int, int, str]] = {}
    parsed = 0
    for path in sorted(sources):
        lang = language_of(path)
        if lang is None:
            continue
        parsed += 1
        lines = sources[path].splitlines()
        defs, refs = regex_spans(lang, sources[path])
        for name, start, end in close_spans(defs, lines, lang == "python"):
            defines[name].add(path)
            if 0 <= start < len(lines) and (path, name) not in info:
                info[(path, name)] = (start, end, lines[start].strip()[:140])
        for name, n in refs.items():
            references[name][path] += n
    keys = rank(sorted(sources), defines, references, info, user_weights)
    return Ranked(keys, info, parsed)


def render(ranked: Ranked, char_budget: int,
           annotate: Optional[Callable[[str, str, int, int], str]] = None) -> SymbolMap:
    """The most definitions that fit `char_budget`, grouped by file in rank order, each cited by
    its line span and, with `annotate`, tagged (the ownership tag)."""
    notes: Dict[Tuple[str, str], str] = {}

    def line_for(key):
        start, end, sig = ranked.info[key]
        if annotate is not None and key not in notes:
            notes[key] = annotate(key[0], key[1], start, end)
        span = f"L{start + 1}" if end <= start else f"L{start + 1}-{end + 1}"
        return f"{span}: {sig}" + (f"  {notes[key]}" if notes.get(key) else "")

    def text_for(keys):
        by_file: Dict[str, list] = defaultdict(list)
        order = []
        for key in keys:
            if key[0] not in by_file:
                order.append(key[0])
            by_file[key[0]].append(line_for(key))
        parts = []
        for path in order:
            parts.append(f"{path}:")
            parts.extend(f"  {line}" for line in by_file[path])
        return "\n".join(parts)

    lo, hi, best, n_best = 0, len(ranked.keys), "", 0
    while lo <= hi:
        mid = (lo + hi) // 2
        text = text_for(ranked.keys[:mid])
        if len(text) <= char_budget:
            best, n_best, lo = text, mid, mid + 1
        else:
            hi = mid - 1
    return SymbolMap(best, ranked.files_parsed, len(ranked.info), n_best, ranked.spans)


__all__ = ["MAX_FILES", "MAX_DEFINERS", "language_of", "select_sources", "pagerank", "rank",
           "rank_sources", "render", "Ranked", "SymbolMap", "close_spans"]
