r"""An independent checker for a rendered resume (issue #299).

The benchmark in `eval/preference_compliance.py` has to judge the enforcement path (the
acceptance rule, the negative-pin gate, the document rules) without using it: a checker that
shares code with what it checks agrees with it even where both are wrong. So this module
imports **nothing from the repository**, only the standard library, and `tests/` asserts it
(`test_the_compliance_checker_shares_no_code_with_the_enforcement_path`). The few lists it
needs (term aliases, the page budget) are copied here on purpose.

It reads the rendered `.tex`, the way IFEval checks a generated text: undo LaTeX escaping
(`C\#`, `\&`, `\%`, `\_`, `\textasciitilde{}`), strip commands, and test the text with code,
never a model. What it knows how to check, from an *expectation* the benchmark states in the
user's terms (items by title and company or name, terms, sections), never by a key:

- `banned_terms`: a word-boundary match, or an alias, anywhere in the document text;
- `absent_items`: an experience (title and company) or project (name) with no heading;
- `required_items`: the same headings, which must be there;
- `absent_sections`: no `%% ART-SECTION: <name>` marker for the section;
- `section_order`: the named sections that are present come first, in that order;
- `max_bullets_per_item`, `max_skills`;
- `max_pages`: the compiled page count when the caller has one, else an estimate from the
  bullets' length (reported as an estimate, never as a measurement).

Reading a page is separate from judging it: `inventory(tex)` lists what is on it (sections in
order, experiences, projects, skills, bullets), which the benchmark also uses to see what a run
dropped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Names a user may use for one thing (copied, not imported: the enforcement path has its own).
# Both directions are checked: a banned "kubernetes" also bans "k8s".
ALIASES: Dict[str, Tuple[str, ...]] = {
    "kubernetes": ("k8s",),
    "postgresql": ("postgres", "psql"),
    "javascript": ("js",),
    "typescript": ("ts",),
    "pytorch": ("torch",),
    "tensorflow": ("tf",),
    "scikit-learn": ("sklearn", "scikit learn"),
    "amazon web services": ("aws",),
    "google cloud platform": ("gcp", "google cloud"),
    "node.js": ("nodejs", "node js"),
    "mongodb": ("mongo",),
    "golang": ("go lang",),
    "github actions": ("gh actions",),
    "weights & biases": ("wandb", "weights and biases"),
    "apache kafka": ("kafka",),
    "apache spark": ("spark", "pyspark"),
    "apache airflow": ("airflow",),
}

# The page budget the enforcement path states (60 bullet lines a page) and the width a bullet
# line holds at \small. Copied so the estimate does not depend on the code it estimates for.
LINES_PER_PAGE = 60
CHARS_PER_LINE = 100
HEADING_LINES = 1.5

SKIP_ENGINE = "no LaTeX engine: the page count was not measured"

# Escapes the formatter writes, and the character each stands for. Replaced by private-use
# placeholders first, so that stripping commands cannot read a `\&` as a command or a `\%` as a
# comment, then put back.
_ESCAPES = (
    ("\\textasciitilde{}", "~"), ("\\textasciitilde", "~"),
    ("\\textasciicircum{}", "^"), ("\\textasciicircum", "^"),
    ("\\textbackslash{}", "\\"), ("\\textbackslash", "\\"),
    ("\\&", "&"), ("\\%", "%"), ("\\$", "$"), ("\\#", "#"), ("\\_", "_"),
    ("\\{", "{"), ("\\}", "}"),
)
_PLACE = {src: chr(0xE000 + i) for i, (src, _) in enumerate(_ESCAPES)}
_MARKER = re.compile(r"^%%\s*ART-SECTION:\s*(\S+)\s*$", re.MULTILINE)
_COMMENT = re.compile(r"(?m)(?<!\\)%.*$")
_BREAK = re.compile(r"\\\\(\[[^\]]*\])?")
_COMMAND = re.compile(r"\\[a-zA-Z]+\*?")
_SPACING = re.compile(r"\\[,;:! ]")
_SECTION_TITLES = {"education": ("education",), "experience": ("experience",),
                   "projects": ("projects",), "skills": ("skills",),
                   "achievements": ("achievements", "awards", "honors")}


# ── reading ──────────────────────────────────────────────────────────────────

def unescape(text: str) -> str:
    """LaTeX escapes undone (`\\#` `\\&` `\\%` `\\_` `\\$` `\\{` `\\}`, `\\textasciitilde{}` and
    its kin). Commands are left in place; `plain` is the whole reading."""
    for src, dst in _ESCAPES:
        text = text.replace(src, dst)
    return text


def strip_commands(text: str) -> str:
    """Comments, line breaks, command names and grouping braces removed; their arguments' text
    is kept. Escapes are not undone here."""
    text = _COMMENT.sub("", text)                  # a comment, on the raw source
    text = _BREAK.sub("\n", text)
    text = _COMMAND.sub(" ", text)
    text = _SPACING.sub(" ", text)
    text = re.sub(r"[{}]", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def plain(text: str) -> str:
    """What a reader sees: escapes protected, commands stripped, escapes undone, spaces
    collapsed."""
    for src, _ in _ESCAPES:
        text = text.replace(src, _PLACE[src])
    text = strip_commands(text)
    for src, dst in _ESCAPES:
        text = text.replace(_PLACE[src], dst)
    return " ".join(text.split())


def _norm(text: str) -> str:
    return plain(text).casefold()


def _group(text: str, start: int) -> Tuple[str, int]:
    """The balanced `{...}` group that begins at the first `{` at or after `start`, and the
    index just past it; ("", start) when there is none."""
    i = text.find("{", start)
    if i < 0:
        return "", start
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return text[i + 1:], len(text)


def _args(text: str, start: int, n: int) -> Tuple[List[str], int]:
    """Up to `n` consecutive `{...}` arguments from `start`; only whitespace may sit between."""
    out: List[str] = []
    pos = start
    for _ in range(n):
        gap = re.match(r"\s*", text[pos:]).end()
        if pos + gap >= len(text) or text[pos + gap] != "{":
            break
        arg, pos = _group(text, pos + gap)
        out.append(arg)
    return out, pos


def sections(tex: str) -> List[Tuple[str, str]]:
    """`[(name, source)]` in page order, from the `%% ART-SECTION:` markers. A document with no
    markers (a hand-written `.tex`) is read by its `\\section{...}` titles instead."""
    body = tex.split("\\begin{document}", 1)[-1].split("\\end{document}", 1)[0]
    marks = list(_MARKER.finditer(body))
    if marks:
        out = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
            out.append((m.group(1).lower(), body[m.end():end]))
        return out
    titles = list(re.finditer(r"\\section\*?\{([^}]*)\}", body))
    out = []
    for i, m in enumerate(titles):
        end = titles[i + 1].start() if i + 1 < len(titles) else len(body)
        title = plain(m.group(1)).casefold()
        name = next((k for k, words in _SECTION_TITLES.items() if any(w in title for w in words)),
                    title)
        out.append((name, body[m.end():end]))
    return out


def _bullets(block: str) -> List[str]:
    out = []
    for m in re.finditer(r"\\resumeItem\s*\{", block):
        text, _ = _group(block, m.end() - 1)
        out.append(plain(text))
    return out


def _experiences(source: str) -> List[Dict[str, Any]]:
    items = []
    for m in re.finditer(r"\\resumeSubheading\b", source):
        args, end = _args(source, m.end(), 4)
        if len(args) < 3:
            continue
        nxt = re.search(r"\\resumeSubheading\b|\\resumeSubHeadingListEnd", source[end:])
        block = source[end:end + nxt.start()] if nxt else source[end:]
        items.append({"title": plain(args[0]), "company": plain(args[2]),
                      "bullets": _bullets(block)})
    return items


def _projects(source: str) -> List[Dict[str, Any]]:
    items = []
    for m in re.finditer(r"\\resumeProjectHeading\b", source):
        args, end = _args(source, m.end(), 2)
        if not args:
            continue
        nxt = re.search(r"\\resumeProjectHeading\b|\\resumeSubHeadingListEnd", source[end:])
        block = source[end:end + nxt.start()] if nxt else source[end:]
        items.append({"name": plain(args[0]), "bullets": _bullets(block)})
    return items


def _skills(source: str) -> List[str]:
    """The skills list: `\\textbf{Category}{: a, b, c}` lines, split on commas."""
    out: List[str] = []
    for m in re.finditer(r"\\textbf\s*\{", source):
        _, end = _group(source, m.end() - 1)
        names, _ = _args(source, end, 1)
        if not names:
            continue
        text = plain(names[0]).lstrip(": ").strip()
        out += [x.strip() for x in text.split(",") if x.strip()]
    return out


def inventory(tex: str) -> Dict[str, Any]:
    """What the page holds: `sections` (names in order, header excluded), `experiences`
    (title, company, bullets), `projects` (name, bullets), `skills`, `achievements` (bullets)
    and `text` (the document's plain text)."""
    secs = sections(tex)
    inv: Dict[str, Any] = {"sections": [n for n, _ in secs if n != "header"],
                           "experiences": [], "projects": [], "skills": [], "achievements": []}
    for name, source in secs:
        if name == "experience":
            inv["experiences"] += _experiences(source)
        elif name == "projects":
            inv["projects"] += _projects(source)
        elif name == "skills":
            inv["skills"] += _skills(source)
        elif name == "achievements":
            inv["achievements"] += _bullets(source)
    body = tex.split("\\begin{document}", 1)[-1].split("\\end{document}", 1)[0]
    inv["text"] = plain(body)
    return inv


def estimated_pages(inv: Dict[str, Any]) -> float:
    """Pages the content would take, estimated from bullet length: each bullet is
    `ceil(chars / CHARS_PER_LINE)` lines, each heading a line and a half. An estimate, for a
    machine with no LaTeX engine; the budget is the same 60 lines a page the executor states."""
    lines = 0.0
    for item in inv["experiences"] + inv["projects"]:
        lines += HEADING_LINES
        for b in item["bullets"]:
            lines += max(1, -(-len(b) // CHARS_PER_LINE))
    lines += HEADING_LINES * len(inv["sections"])
    if inv["skills"]:
        lines += max(1, -(-len(", ".join(inv["skills"])) // CHARS_PER_LINE))
    for b in inv["achievements"]:
        lines += max(1, -(-len(b) // CHARS_PER_LINE))
    return lines / LINES_PER_PAGE


# ── judging ──────────────────────────────────────────────────────────────────

def _forms(term: str, extra: Iterable[str] = ()) -> List[str]:
    """`term` and every spelling the alias list ties to it, either way round."""
    t = " ".join(term.casefold().split())
    out = {t, *(" ".join(e.casefold().split()) for e in extra)}
    for name, aliases in ALIASES.items():
        group = {name, *aliases}
        if t in group:
            out |= group
    return sorted(out)


def mentions(text: str, term: str, aliases: Iterable[str] = (), raw: bool = False) -> Optional[str]:
    """The form of `term` found in `text` as a whole word (or phrase), else None. `text` is plain
    text, as `inventory` returns it; `raw=True` reads LaTeX source first (a plain `%` would
    otherwise start a comment)."""
    hay = (plain(text) if raw else text).casefold()
    for form in _forms(term, aliases):
        # Alphanumeric boundaries, so C# and C++ and Node.js are read whole, and "go" is not "google".
        if re.search(r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])", hay):
            return form
    return None


def _same_item(item: Dict[str, Any], want: Dict[str, Any]) -> bool:
    if want.get("kind") == "experience":
        return (_norm(item.get("title", "")) == _norm(want["title"])
                and _norm(item.get("company", "")) == _norm(want["company"]))
    return _norm(item.get("name", "")) == _norm(want["name"])


def check(tex: str, expect: Dict[str, Any], pages: Optional[int] = None) -> Dict[str, Any]:
    """Judge one rendered page against `expect`. Returns `{"violations": [{check, detail,
    source}], "skipped": [reason, ...], "inventory": inventory}`. `source` is the label the
    benchmark gave the expectation (the preference or rule it came from). `pages` is the
    compiled page count, or None when there was no engine to count with."""
    inv = inventory(tex)
    out: List[Dict[str, Any]] = []
    skipped: List[str] = []

    def bad(name: str, detail: Any, source: Any) -> None:
        out.append({"check": name, "detail": detail, "source": source})

    for want in expect.get("banned_terms") or []:
        form = mentions(inv["text"], want["term"], want.get("aliases") or ())
        if form:
            bad("banned_term", f"{want['term']!r} appears as {form!r}", want.get("source"))
    for want in expect.get("absent_items") or []:
        pool = inv["experiences"] if want.get("kind") == "experience" else inv["projects"]
        if any(_same_item(it, want) for it in pool):
            bad("suppressed_item", f"{want.get('title') or want.get('name')!r} is on the page",
                want.get("source"))
    for want in expect.get("absent_sections") or []:
        if want["name"] in inv["sections"]:
            bad("excluded_section", f"section {want['name']!r} is on the page", want.get("source"))
    for want in expect.get("required_items") or []:
        pool = inv["experiences"] if want.get("kind") == "experience" else inv["projects"]
        if not any(_same_item(it, want) for it in pool):
            bad("required_item", f"{want.get('title') or want.get('name')!r} is missing",
                want.get("source"))
    order = expect.get("section_order")
    if order:
        named = [s for s in order["order"] if s in inv["sections"]]
        if inv["sections"][:len(named)] != named:
            bad("section_order", f"sections read {inv['sections']}, expected {named} first",
                order.get("source"))
    limit = expect.get("max_bullets_per_item")
    if limit:
        for item in inv["experiences"] + inv["projects"]:
            if len(item["bullets"]) > limit["value"]:
                bad("bullets_per_item", f"{item.get('title') or item.get('name')!r} has "
                    f"{len(item['bullets'])} bullets, over {limit['value']}", limit.get("source"))
    cap = expect.get("max_skills")
    if cap and len(inv["skills"]) > cap["value"]:
        bad("max_skills", f"{len(inv['skills'])} skills, over {cap['value']}", cap.get("source"))
    budget = expect.get("max_pages")
    if budget:
        if pages is not None:
            if pages > budget["value"]:
                bad("max_pages", f"{pages} pages, over {budget['value']}", budget.get("source"))
        else:
            skipped.append(SKIP_ENGINE)
            est = estimated_pages(inv)
            if est > budget["value"]:
                bad("max_pages_estimated",
                    f"about {est:.2f} pages (an estimate), over {budget['value']}",
                    budget.get("source"))
    return {"violations": out, "skipped": sorted(set(skipped)), "inventory": inv}
