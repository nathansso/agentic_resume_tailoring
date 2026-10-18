"""A symbol map seeded by authorship, in the manner of Aider's repo map (issue #397, rung 5).

Definitions and references are read with tree-sitter where a grammar is installed (Python,
JavaScript, TypeScript/TSX), and with line regexes otherwise (Go, or any language when
tree-sitter is absent). Files are nodes; an edge runs from a file that references a name to the
file that defines it. Personalized PageRank, seeded with the user's changed files weighted by the
lines they changed, ranks the files; each file's rank is shared out over the definitions it
points at, and the best definitions are rendered, one signature line each, up to a token budget.

`backend="regex"` forces the light fallback for every language, so the eval can price
tree-sitter against it (rung `5r`).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx

# ── tree-sitter, when installed ──────────────────────────────────────────────

_TS_QUERIES = {
    "python": {
        "def": [
            "(function_definition name: (identifier) @n)",
            "(class_definition name: (identifier) @n)",
        ],
        "ref": [
            "(call function: (identifier) @n)",
            "(call function: (attribute attribute: (identifier) @n))",
            "(import_from_statement name: (dotted_name (identifier) @n))",
        ],
    },
    "javascript": {
        "def": [
            "(function_declaration name: (identifier) @n)",
            "(class_declaration name: (identifier) @n)",
            "(method_definition name: (property_identifier) @n)",
            "(variable_declarator name: (identifier) @n value: (arrow_function))",
            "(variable_declarator name: (identifier) @n value: (function_expression))",
        ],
        "ref": [
            "(call_expression function: (identifier) @n)",
            "(call_expression function: (member_expression property: (property_identifier) @n))",
            "(new_expression constructor: (identifier) @n)",
            "(jsx_opening_element name: (identifier) @n)",
            "(jsx_self_closing_element name: (identifier) @n)",
        ],
    },
}
_TS_QUERIES["typescript"] = {
    "def": _TS_QUERIES["javascript"]["def"] + [
        "(class_declaration name: (type_identifier) @n)",
        "(abstract_class_declaration name: (type_identifier) @n)",
        "(interface_declaration name: (type_identifier) @n)",
        "(type_alias_declaration name: (type_identifier) @n)",
    ],
    "ref": _TS_QUERIES["javascript"]["ref"] + ["(type_identifier) @n"],
}
_TS_QUERIES["tsx"] = _TS_QUERIES["typescript"]

_EXT_LANG = {
    ".py": "python", ".ipynb": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".go": "go",
}

_ts_cache: dict = {}


def tree_sitter_available() -> bool:
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return False
    return True


def _ts_language(lang: str):
    """(Language, compiled def queries, compiled ref queries), or None if not installed."""
    if lang in _ts_cache:
        return _ts_cache[lang]
    result = None
    try:
        from tree_sitter import Language, Query
        if lang == "python":
            import tree_sitter_python as mod
            ptr = mod.language()
        elif lang == "javascript":
            import tree_sitter_javascript as mod
            ptr = mod.language()
        elif lang == "typescript":
            import tree_sitter_typescript as mod
            ptr = mod.language_typescript()
        elif lang == "tsx":
            import tree_sitter_typescript as mod
            ptr = mod.language_tsx()
        else:
            ptr = None
        if ptr is not None:
            language = Language(ptr)
            compiled = {}
            for kind in ("def", "ref"):
                compiled[kind] = []
                for src in _TS_QUERIES[lang][kind]:
                    try:
                        compiled[kind].append(Query(language, src))
                    except Exception:  # a node type this grammar version lacks
                        pass
            result = (language, compiled["def"], compiled["ref"])
    except ImportError:
        result = None
    _ts_cache[lang] = result
    return result


def _ts_tags(lang: str, code: str):
    loaded = _ts_language(lang)
    if loaded is None:
        return None
    from tree_sitter import Parser, QueryCursor
    language, def_queries, ref_queries = loaded
    tree = Parser(language).parse(code.encode("utf-8", errors="replace"))
    defs, refs = [], []
    for queries, out in ((def_queries, defs), (ref_queries, refs)):
        for q in queries:
            for nodes in QueryCursor(q).captures(tree.root_node).values():
                for node in nodes:
                    out.append((node.text.decode("utf-8", errors="replace"), node.start_point[0]))
    return defs, refs


# ── the regex fallback ───────────────────────────────────────────────────────

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


def _regex_tags(lang: str, code: str):
    rx = _RX_DEF.get(lang)
    if rx is None:
        return [], []
    defs = []
    for i, line in enumerate(code.splitlines()):
        m = rx.match(line)
        if m:
            name = next((g for g in m.groups() if g), None)
            if name:
                defs.append((name, i))
    refs = [(m.group(0), -1) for m in _WORD.finditer(code)]
    return defs, refs


# ── the map ──────────────────────────────────────────────────────────────────

@dataclass
class SymbolMap:
    text: str
    backend: str          # "tree-sitter", "regex" or "mixed"
    files_parsed: int
    definitions: int
    rendered: int


def language_of(path: str) -> str | None:
    for ext, lang in _EXT_LANG.items():
        if path.lower().endswith(ext):
            return lang
    return None


def build_symbol_map(sources: dict[str, str], user_weights: dict[str, float] | None,
                     token_budget: int = 2048, backend: str = "auto") -> SymbolMap:
    """Rank the definitions in `sources` (path -> code; notebooks already reduced to their code
    cells) and render the best of them within `token_budget` (estimated at 4 chars a token)."""
    defines: dict[str, set] = defaultdict(set)
    references: dict[str, Counter] = defaultdict(Counter)
    def_lines: dict[tuple, str] = {}
    used = set()
    for path, code in sources.items():
        lang = language_of(path)
        if lang is None:
            continue
        tags = None
        if backend in ("auto", "tree-sitter"):
            tags = _ts_tags(lang, code)
            if tags is not None:
                used.add("tree-sitter")
        if tags is None:
            tags = _regex_tags(lang, code)
            used.add("regex")
        defs, refs = tags
        lines = code.splitlines()
        for name, line in defs:
            defines[name].add(path)
            if 0 <= line < len(lines) and (path, name) not in def_lines:
                def_lines[(path, name)] = lines[line].strip()[:110]
        for name, _ in refs:
            references[name][path] += 1

    user_weights = {p: w for p, w in (user_weights or {}).items() if p in sources and w > 0}
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(sources)
    for name in set(defines) & set(references):
        definers = defines[name]
        mul = 1.0
        if len(name) >= 8 and ("_" in name or re.search(r"[a-z][A-Z]", name)):
            mul *= 10
        if name.startswith("_"):
            mul *= 0.1
        if len(definers) > 5:
            mul *= 0.1
        for referencer, n in references[name].items():
            w = mul * math.sqrt(n) * (5 if referencer in user_weights else 1)
            for definer in definers:
                graph.add_edge(referencer, definer, weight=w, ident=name)

    total = sum(user_weights.values())
    pers = {node: user_weights.get(node, 0.0) / total for node in graph.nodes} if total else None
    try:
        rank = nx.pagerank(graph, weight="weight", personalization=pers, dangling=pers)
    except Exception:  # e.g. no convergence on a degenerate graph
        rank = {node: 1.0 / max(1, len(graph)) for node in graph.nodes}

    scores: dict[tuple, float] = defaultdict(float)
    for src in graph.nodes:
        out = list(graph.out_edges(src, data=True))
        weight = sum(d["weight"] for _, _, d in out)
        for _, dst, d in out:
            scores[(dst, d["ident"])] += rank[src] * d["weight"] / weight
    for key in def_lines:  # unreferenced definitions still rank, below any referenced one
        scores.setdefault(key, rank.get(key[0], 0.0) * 0.01)
    ranked = sorted((k for k in scores if k in def_lines), key=lambda k: (-scores[k], k))

    def render(keys):
        by_file: dict[str, list] = defaultdict(list)
        order = []
        for path, name in keys:
            if path not in by_file:
                order.append(path)
            by_file[path].append(def_lines[(path, name)])
        parts = []
        for path in order:
            parts.append(f"{path}:")
            parts.extend(f"  {line}" for line in by_file[path])
        return "\n".join(parts)

    lo, hi, best = 0, len(ranked), ""
    while lo <= hi:  # the most definitions that fit the budget
        mid = (lo + hi) // 2
        text = render(ranked[:mid])
        if len(text) / 4 <= token_budget:
            best, lo = text, mid + 1
        else:
            hi = mid - 1
    rendered = sum(1 for line in best.splitlines() if line.startswith("  "))
    backend_used = "mixed" if len(used) > 1 else (used.pop() if used else "none")
    return SymbolMap(best, backend_used, len({p for p in sources if language_of(p)}),
                     len(def_lines), rendered)
