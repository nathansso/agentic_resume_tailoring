"""A symbol map seeded by authorship, in the manner of Aider's repo map (issue #397, rung 5).

Definitions and references are read with tree-sitter where a grammar is installed (Python,
JavaScript, TypeScript/TSX), and with line regexes otherwise (Go, or any language when
tree-sitter is absent). Files are nodes; an edge runs from a file that references a name to the
file that defines it. Personalized PageRank, seeded with the user's changed files weighted by the
lines they changed, ranks the files; each file's rank is shared out over the definitions it
points at, and the best definitions are rendered, one signature line each, up to a token budget.

`backend="regex"` forces the light fallback for every language, so the eval can price
tree-sitter against it (rung `5r`).

The variants added to finish #397 (`variants.py`) bring four more backends, all producing the
same (name, start line, end line) definitions so the ranking and rendering stay shared:

- `ts-tags`: Aider's method proper — each grammar's own `queries/tags.scm` (shipped inside the
  `tree-sitter-<lang>` wheels), `@definition.*` and `@reference.*` captures, for Python,
  JavaScript, TypeScript/TSX and Go;
- `ast`: Python's standard-library `ast` (exact spans, signatures with arguments, decorators),
  with the regex fallback for every other language;
- `ctags`: Universal Ctags' JSON output (`--fields=+neS`), definitions only, for every language
  it knows, with word references; needs a `ctags` binary (`ART_CTAGS` or PATH);
- `pygments`: Pygments' lexers (pure Python, ~600 languages): `Name.Function` / `Name.Class`
  tokens as definitions, `Name` tokens as references.
"""

from __future__ import annotations

import ast as pyast
import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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


# ── the variant backends (#397 follow-up) ────────────────────────────────────
# Each returns (defs, refs): defs as (name, start line, end line), 0-based, end inclusive;
# refs as (name, line). An end of -1 means unknown and is estimated from the next definition.

def _regex_spans(lang: str, code: str):
    defs, refs = _regex_tags(lang, code)
    return [(n, line, -1) for n, line in defs], refs


_TAGS_MODULES = {"python": ("tree_sitter_python", "language"),
                 "javascript": ("tree_sitter_javascript", "language"),
                 "typescript": ("tree_sitter_typescript", "language_typescript"),
                 "tsx": ("tree_sitter_typescript", "language_tsx"),
                 "go": ("tree_sitter_go", "language")}
_tags_cache: dict = {}


def _grammar_tags_query(module) -> str:
    path = Path(module.__file__).parent / "queries" / "tags.scm"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _tags_language(lang: str):
    """(Language, Query) from the grammar wheel's own tags.scm, as Aider uses it; None if absent.
    TypeScript's tags.scm only adds to JavaScript's, so the two are joined for TS and TSX."""
    if lang in _tags_cache:
        return _tags_cache[lang]
    result = None
    try:
        import importlib

        from tree_sitter import Language, Query
        mod_name, fn = _TAGS_MODULES[lang]
        mod = importlib.import_module(mod_name)
        language = Language(getattr(mod, fn)())
        source = _grammar_tags_query(mod)
        if lang in ("typescript", "tsx"):
            source = _grammar_tags_query(importlib.import_module("tree_sitter_javascript")) + "\n" + source
        # compile pattern by pattern: drop any a grammar version rejects
        patterns, depth, cur = [], 0, []
        for line in source.splitlines():
            if not cur and (not line.strip() or line.lstrip().startswith(";")):
                continue
            cur.append(line)
            depth += line.count("(") - line.count(")") + line.count("[") - line.count("]")
            if depth <= 0:
                patterns.append("\n".join(cur))
                cur, depth = [], 0
        good = []
        for p in patterns:
            try:
                Query(language, p)
                good.append(p)
            except Exception:
                pass
        result = (language, Query(language, "\n".join(good))) if good else None
    except (ImportError, KeyError, AttributeError):
        result = None
    _tags_cache[lang] = result
    return result


def _ts_tags_spans(lang: str, code: str):
    loaded = _tags_language(lang)
    if loaded is None:
        return None
    from tree_sitter import Parser, QueryCursor
    language, query = loaded
    tree = Parser(language).parse(code.encode("utf-8", errors="replace"))
    defs, refs = [], []
    for _, caps in QueryCursor(query).matches(tree.root_node):
        names = caps.get("name") or []
        kinds = [k for k in caps if k.startswith(("definition.", "reference."))]
        for name_node in names:
            text = name_node.text.decode("utf-8", errors="replace")
            for k in kinds:
                outer = caps[k][0]
                if k.startswith("definition."):
                    defs.append((text, outer.start_point[0], outer.end_point[0]))
                else:
                    refs.append((text, name_node.start_point[0]))
    defs = sorted(set(defs), key=lambda d: (d[1], d[0]))
    return defs, sorted(refs, key=lambda r: (r[1], r[0]))


def _ast_spans(lang: str, code: str):
    """Python by the standard library's parser; every other language by the regex fallback.
    Definitions keep their exact span and a signature with arguments and decorators."""
    if lang != "python":
        return _regex_spans(lang, code)
    try:
        tree = pyast.parse(code)
    except (SyntaxError, ValueError):
        return _regex_spans(lang, code)
    defs, refs = [], []
    for node in pyast.walk(tree):
        if isinstance(node, (pyast.FunctionDef, pyast.AsyncFunctionDef, pyast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            defs.append((node.name, start, (node.end_lineno or node.lineno) - 1))
        elif isinstance(node, pyast.Call):
            f = node.func
            name = f.id if isinstance(f, pyast.Name) else f.attr if isinstance(f, pyast.Attribute) else None
            if name:
                refs.append((name, node.lineno - 1))
        elif isinstance(node, pyast.Name) and isinstance(node.ctx, pyast.Load):
            refs.append((node.id, node.lineno - 1))
        elif isinstance(node, pyast.ImportFrom):
            for alias in node.names:
                refs.append((alias.name, node.lineno - 1))
    defs.sort(key=lambda d: (d[1], d[0]))
    refs.sort(key=lambda r: (r[1], r[0]))
    return defs, refs


def ast_signature(code_lines: list[str], start: int, end: int) -> str:
    """A Python definition's header as one line: decorators, then `def name(args) -> ret`."""
    out, i = [], start
    while i <= end and i < len(code_lines):
        line = code_lines[i].strip()
        out.append(line)
        if re.match(r"(async\s+)?(def|class)\b", line):
            depth = line.count("(") - line.count(")")
            while depth > 0 and i + 1 <= end and i + 1 < len(code_lines):
                i += 1
                nxt = code_lines[i].strip()
                out.append(nxt)
                depth += nxt.count("(") - nxt.count(")")
            break
        i += 1
    return re.sub(r"\s+", " ", " ".join(out))


def ctags_binary() -> str | None:
    return os.environ.get("ART_CTAGS") or shutil.which("ctags") or shutil.which("uctags")


def _ctags_spans_for(sources: dict[str, str], root: Path | None):
    """Universal Ctags over the files on disk (it reads paths, not strings); one process for
    all files. Returns path -> (defs, refs), or None if no ctags binary."""
    exe = ctags_binary()
    if exe is None or root is None:
        return None
    paths = sorted(p for p in sources if not p.lower().endswith(".ipynb"))
    out: dict[str, tuple] = {p: ([], []) for p in sources}
    if paths:
        done = subprocess.run([exe, "--output-format=json", "--fields=+neS", "--sort=no",
                               "--extras=-F", "-L", "-", "-f", "-"],
                              input="\n".join(paths), cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        kinds = {"function", "method", "class", "member", "struct", "interface", "type", "func",
                 "enum", "trait", "module", "constructor", "typedef", "alias"}
        for line in done.stdout.splitlines():
            try:
                tag = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = tag.get("path", "").replace("\\", "/")
            if p in out and tag.get("kind") in kinds and tag.get("line"):
                start = int(tag["line"]) - 1
                end = int(tag.get("end", tag["line"])) - 1
                out[p][0].append((tag["name"], start, end))
    for p, code in sources.items():
        defs = sorted(set(out[p][0]), key=lambda d: (d[1], d[0]))
        if p.lower().endswith(".ipynb"):
            defs = _ast_spans("python", code)[0]  # ctags can't read notebook JSON
        out[p] = (defs, [(m.group(0), -1) for m in _WORD.finditer(code)])
    return out


def _pygments_spans(path: str, code: str):
    try:
        from pygments.lexers import get_lexer_for_filename
        from pygments.token import Name
        from pygments.util import ClassNotFound
    except ImportError:
        return None
    try:
        lexer = get_lexer_for_filename(path, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return [], []
    defs, refs, line = [], [], 0
    for tok, val in lexer.get_tokens(code):
        if tok in Name.Function or tok in Name.Class:
            if tok not in Name.Function.Magic:
                defs.append((val, line, -1))
        elif tok in Name:
            refs.append((val, line))
        line += val.count("\n")
    return sorted(set(defs), key=lambda d: (d[1], d[0])), refs


def spans_for(backend: str, sources: dict[str, str], root: Path | None = None) -> tuple[dict, set]:
    """path -> (defs, refs) for one of the variant backends, and the backends actually used."""
    used: set[str] = set()
    out: dict[str, tuple] = {}
    if backend == "ctags":
        got = _ctags_spans_for(sources, root)
        if got is not None:
            return got, {"ctags"}
        backend = "regex"
    for path, code in sources.items():
        lang = language_of(path)
        tags = None
        if backend == "ts-tags" and lang:
            tags = _ts_tags_spans(lang, code)
            if tags is not None:
                used.add("ts-tags")
        elif backend == "ast" and lang:
            tags = _ast_spans(lang, code)
            used.add("ast" if lang == "python" else "regex")
        elif backend == "pygments":
            tags = _pygments_spans(path, code)
            if tags is not None:
                used.add("pygments")
        if tags is None:
            if not lang:
                continue
            tags = _regex_spans(lang, code)
            used.add("regex")
        out[path] = tags
    return out, used


def _close_spans(defs: list[tuple], lines: list[str], indented: bool = False) -> list[tuple]:
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


# ── the map ──────────────────────────────────────────────────────────────────

@dataclass
class SymbolMap:
    text: str
    backend: str          # "tree-sitter", "regex" or "mixed"
    files_parsed: int
    definitions: int
    rendered: int
    ranked: list = field(default_factory=list)   # [(path, name, start, end)] best first


def language_of(path: str) -> str | None:
    for ext, lang in _EXT_LANG.items():
        if path.lower().endswith(ext):
            return lang
    return None


def _rank(sources, defines: dict, references: dict, def_keys, user_weights) -> list[tuple]:
    """(path, name) definition keys, best first: personalized PageRank over the file graph,
    each file's rank shared out over the definitions it references."""
    def_lines = def_keys
    user_weights = {p: w for p, w in (user_weights or {}).items() if p in sources and w > 0}
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(sources)
    for name in sorted(set(defines) & set(references)):  # sorted: the ranking is reproducible
        definers = sorted(defines[name])
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
    return sorted((k for k in scores if k in def_lines), key=lambda k: (-scores[k], k))


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

    ranked = _rank(sources, defines, references, def_lines, user_weights)

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


def build_symbol_map_v2(sources: dict[str, str], user_weights: dict[str, float] | None,
                        token_budget: int, backend: str, root: Path | None = None,
                        annotate: Callable[[str, str, int, int], str] | None = None,
                        chars_per_token: float = 3.6) -> SymbolMap:
    """The variants' symbol map: the same ranking as `build_symbol_map`, over one of the variant
    backends (`regex`, `ts-tags`, `ast`, `ctags`, `pygments`), with each definition cited by its
    line span (`L12-40`) and, if `annotate` is given, a suffix from it (the ownership tag)."""
    spans, used = spans_for(backend, sources, root)
    defines: dict[str, set] = defaultdict(set)
    references: dict[str, Counter] = defaultdict(Counter)
    def_info: dict[tuple, tuple] = {}     # (path, name) -> (start, end, signature)
    for path in sorted(spans):
        defs, refs = spans[path]
        lines = sources[path].splitlines()
        for name, start, end in _close_spans(defs, lines, language_of(path) == "python"):
            defines[name].add(path)
            if 0 <= start < len(lines) and (path, name) not in def_info:
                sig = (ast_signature(lines, start, end) if backend == "ast" and path.endswith(".py")
                       else lines[start].strip())
                def_info[(path, name)] = (start, end, sig[:140])
        for name, _ in refs:
            references[name][path] += 1
    ranked = _rank(sources, defines, references, def_info, user_weights)
    notes: dict[tuple, str] = {}

    def line_for(key):
        start, end, sig = def_info[key]
        if annotate is not None and key not in notes:
            notes[key] = annotate(key[0], key[1], start, end)
        span = f"L{start + 1}" if end <= start else f"L{start + 1}-{end + 1}"
        return f"{span}: {sig}" + (f"  {notes[key]}" if notes.get(key) else "")

    def render(keys):
        by_file: dict[str, list] = defaultdict(list)
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

    lo, hi, best, n_best = 0, len(ranked), "", 0
    while lo <= hi:
        mid = (lo + hi) // 2
        text = render(ranked[:mid])
        if len(text) / chars_per_token <= token_budget:
            best, n_best, lo = text, mid, mid + 1
        else:
            hi = mid - 1
    backend_used = "mixed" if len(used) > 1 else (next(iter(used)) if used else "none")
    top = [(p, n, def_info[(p, n)][0], def_info[(p, n)][1]) for p, n in ranked]
    return SymbolMap(best, backend_used, len(spans), len(def_info), n_best, top)
