"""What a hand edit of the `.tex` changed, in terms a host can act on (issue #278).

The editor saves the user's `.tex` as a node's `edited_tex`, next to a `content`
that still holds the pre-edit bullets, so the host saw only "tex changed". This
module reads two versions of the source back into items and bullets, using the
`%% ART-SECTION` markers (#71) and the formatter's headings, and reports:

- `bullets`: per item, a bullet revised, added or removed (the bullet id on the
  earlier version, its text before and after);
- `reordered`: items whose bullets only moved;
- `section_order`: sections that moved;
- `unmapped`: a region whose lines changed outside any bullet (headings, dates,
  the header, skills, the preamble, a deleted marker) as a unified diff, flagged
  unmapped. A change is never reported as nothing.

`uncarried` is the executor's side (`discards_editor_edits`): the part of a node's
tex edit that a new version's content does not already hold.

Model-free by rule (`tests/test_harness_boundary.py`): this is a parser, not a
judgement. The user's raw tex is never folded back into `content`, because content
renders through the support check and raw tex would not.
"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any, Dict, List, Optional, Tuple

from agents.checks import exp_key, proj_key

CARRYABLE = ("exp:", "proj:")        # items a plan node can address (`program.ProgramNode.item_key`)
_MARKER = re.compile(r"^\s*%%\s*ART-SECTION:\s*(\S+)")
_SECTION = re.compile(r"^\\section\{(.*)\}")
_SUBHEADING = re.compile(r"^\\resumeSubheading(?![A-Za-z])")
_PROJECT = re.compile(r"^\\resumeProjectHeading(?![A-Za-z])")
_ITEM = "\\resumeItem{"
_PLAIN = {"&": "&", "%": "%", "$": "$", "#": "#", "_": "_", "{": "{", "}": "}"}
_WORDS = {"textasciitilde": "~", "textasciicircum": "^", "textbackslash": "\\"}


# ── tex text <-> the markdown-ish bullet text content holds ──────────────────

def _close(s: str, i: int) -> int:
    """Index of the `}` matching the `{` at `s[i]`, or -1. `\\{` and `\\}` do not count."""
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def untex(s: str) -> str:
    """The inverse of the formatter's `_convert_inline(_escape_tex(...))`: `\\textbf{x}` back to
    `**x**`, `\\href` to `[label](url)`, escapes undone. Anything else is kept as written."""
    out: List[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if nxt in _PLAIN:
            out.append(_PLAIN[nxt])
            i += 2
            continue
        m = re.match(r"\\([A-Za-z]+)", s[i:])
        if not m:
            out.append(s[i:i + 2])
            i += 2
            continue
        name, end = m.group(1), i + m.end()
        if name in _WORDS and s[end:end + 2] == "{}":
            out.append(_WORDS[name])
            i = end + 2
        elif name in ("textbf", "textit", "emph") and s[end:end + 1] == "{":
            j = _close(s, end)
            if j < 0:
                out.append(s[i:end])
                i = end
                continue
            mark = "**" if name == "textbf" else "*"
            out.append(mark + untex(s[end + 1:j]) + mark)
            i = j + 1
        elif name == "href" and s[end:end + 1] == "{":
            j = _close(s, end)
            k = _close(s, j + 1) if j > 0 and s[j + 1:j + 2] == "{" else -1
            if j < 0 or k < 0:
                out.append(s[i:end])
                i = end
                continue
            label = s[j + 2:k]
            if label.startswith("\\underline{") and _close(label, len("\\underline")) == len(label) - 1:
                label = label[len("\\underline{"):-1]
            out.append(f"[{untex(label)}]({untex(s[end + 1:j])})")
            i = k + 1
        else:
            out.append(s[i:end])
            i = end
    return re.sub(r"\s+", " ", "".join(out)).strip()


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# ── reading the source ───────────────────────────────────────────────────────

def _groups(text: str, need: int) -> List[str]:
    """Up to `need` consecutive `{...}` groups at the start of `text`."""
    out, i = [], 0
    while len(out) < need:
        while i < len(text) and text[i].isspace():
            i += 1
        if text[i:i + 1] != "{":
            break
        j = _close(text, i)
        if j < 0:
            break
        out.append(text[i + 1:j])
        i = j + 1
    return out


def _first_bold(group: str) -> str:
    i = group.find("\\textbf{")
    if i < 0:
        return untex(group)
    j = _close(group, i + len("\\textbf"))
    return untex(group[i + len("\\textbf{"):j] if j > 0 else group)


class Parsed:
    """A `.tex` read back into sections, items and bullets."""

    def __init__(self) -> None:
        self.sections: List[str] = []                         # ART-SECTION keys, document order
        self.items: Dict[str, Dict[str, Any]] = {}            # item key -> {title, section, bullets}
        self.skeleton: Dict[str, List[Tuple[Optional[str], str]]] = {}   # section -> (item, line)


def parse(tex: str) -> Parsed:
    lines = (tex or "").replace("\r\n", "\n").split("\n")
    marked = any(_MARKER.match(ln) for ln in lines)
    out = Parsed()
    section, item = "preamble", None
    i = 0

    def keep(line: str) -> None:
        text = norm(line)
        if text:
            out.skeleton.setdefault(section, []).append((item, text))

    def start(key: str) -> None:
        nonlocal section, item
        section, item = key, None
        if key not in ("preamble", "document") and key not in out.sections:
            out.sections.append(key)

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        marker = _MARKER.match(raw)
        if marker:
            start(marker.group(1))
        elif section == "preamble" and line.startswith("\\begin{document}"):
            keep(raw)
            if not marked:
                start("document")
        elif not marked and section != "preamble" and _SECTION.match(line):
            start(norm(_SECTION.match(line).group(1)).lower() or "document")
            keep(raw)
        elif line.startswith(_ITEM):
            text, j = line, i
            while _close(text, len(_ITEM) - 1) < 0 and j + 1 < len(lines):
                j += 1
                text += " " + lines[j].strip()
            end = _close(text, len(_ITEM) - 1)
            bullet = untex(text[len(_ITEM):end] if end > 0 else text[len(_ITEM):])
            key = item or section
            slot = out.items.setdefault(key, {"title": _label(section) if item is None else "",
                                              "section": section, "bullets": []})
            slot["bullets"].append(bullet)
            i = j
        elif _SUBHEADING.match(line) or _PROJECT.match(line):
            sub = bool(_SUBHEADING.match(line))
            need = 4 if sub else 2
            macro = re.match(r"\\[A-Za-z]+", line).group(0)
            text, j = line[len(macro):], i
            while (len(_groups(text, need)) < need and j + 1 < len(lines) and j - i < 4
                   and not lines[j + 1].strip().startswith(("\\", "%"))):
                j += 1
                text += " " + lines[j].strip()
            g = _groups(text, need)
            consumed = lines[i:j + 1]
            if len(g) == need:
                if sub:
                    title, company = untex(g[0]), untex(g[2])
                    item = exp_key({"title": title, "company": company})
                    label = f"{title} @ {company}"
                else:
                    name = _first_bold(g[0])
                    item = proj_key({"name": name})
                    label = name
                out.items.setdefault(item, {"title": label, "section": section, "bullets": []})
            else:
                item = None
            for ln in consumed:
                keep(ln)
            i = j
        else:
            if re.match(r"\\resumeSubHeadingListEnd", line):
                keep(raw)
                item = None
            elif line:
                keep(raw)
        i += 1
    return out


def _label(section: str) -> str:
    return section.replace("_", " ").title()


# ── comparing two versions of the source ─────────────────────────────────────

def _unified(before: List[str], after: List[str], limit: int = 60) -> str:
    diff = list(difflib.unified_diff(before, after, "before", "after", n=1, lineterm=""))
    if len(diff) > limit:
        diff = diff[:limit] + [f"... ({len(diff) - limit} more lines)"]
    return "\n".join(diff)


def _item_edits(key: str, title: str, before: List[str], after: List[str], whole: Optional[str]):
    """Bullet edits for one item, plus its bullets when they only moved."""
    edits: List[Dict[str, Any]] = []
    moved = False
    if whole == "removed":
        return [dict(item=key, title=title, change="removed", bullet=f"{key}#b{n}",
                     before=b, after=None, position=None) for n, b in enumerate(before)], False
    if whole == "added":
        return [dict(item=key, title=title, change="added", bullet=None, before=None,
                     after=a, position=n) for n, a in enumerate(after)], False
    if before == after:
        return [], False
    if sorted(before) == sorted(after):
        return [], True
    gone: List[Dict[str, Any]] = []
    new: List[Dict[str, Any]] = []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, before, after,
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, nxt = list(range(a0, a1)), list(range(b0, b1))
        for x, y in zip(old, nxt):
            edits.append(dict(item=key, title=title, change="revised", bullet=f"{key}#b{x}",
                              before=before[x], after=after[y], position=y))
        for x in old[len(nxt):]:
            gone.append(dict(item=key, title=title, change="removed", bullet=f"{key}#b{x}",
                             before=before[x], after=None, position=None))
        for y in nxt[len(old):]:
            new.append(dict(item=key, title=title, change="added", bullet=None, before=None,
                            after=after[y], position=y))
    # A bullet that left one place and came back in another only moved.
    for g in list(gone):
        twin = next((n for n in new if n["after"] == g["before"]), None)
        if twin is not None:
            gone.remove(g)
            new.remove(twin)
            moved = True
    return edits + gone + new, moved


def compare(before_tex: str, after_tex: str) -> Dict[str, Any]:
    """What changed from `before_tex` to `after_tex`. All four lists empty means the two read as
    the same resume (whitespace aside)."""
    b, a = parse(before_tex), parse(after_tex)
    bullets: List[Dict[str, Any]] = []
    reordered: List[Dict[str, Any]] = []
    lone: set = set()                       # items on one side only: reported by their bullets
    for key in list(b.items) + [k for k in a.items if k not in b.items]:
        ib, ia = b.items.get(key), a.items.get(key)
        if ib is None or ia is None:
            if (ia or ib)["bullets"]:
                lone.add(key)
        whole = "added" if ib is None else "removed" if ia is None else None
        base = ib or ia
        edits, moved = _item_edits(key, base["title"], (ib or {}).get("bullets", []),
                                   (ia or {}).get("bullets", []), whole)
        bullets += edits
        if moved:
            reordered.append({"item": key, "title": base["title"], "order": ia["bullets"]})
    unmapped: List[Dict[str, Any]] = []
    for sec in list(b.skeleton) + [s for s in a.skeleton if s not in b.skeleton]:
        lb = [t for it, t in b.skeleton.get(sec, []) if it not in lone]
        la = [t for it, t in a.skeleton.get(sec, []) if it not in lone]
        if lb != la:
            side = "" if sec in b.skeleton and sec in a.skeleton else (
                " (the section is missing from the edited source)" if sec not in a.skeleton
                else " (a section the earlier version did not have)")
            unmapped.append({
                "region": sec,
                "reason": "lines outside the bullets changed (headings, dates, the header, skills, "
                          f"layout commands or the section markers){side}",
                "diff": _unified(lb, la)})
    common = [s for s in b.sections if s in a.sections]
    after_common = [s for s in a.sections if s in b.sections]
    order = {"before": b.sections, "after": a.sections} if common != after_common else None
    return {"bullets": bullets, "reordered": reordered, "section_order": order,
            "unmapped": unmapped}


def is_empty(edits: Optional[Dict[str, Any]]) -> bool:
    return not edits or not (edits.get("bullets") or edits.get("reordered")
                             or edits.get("section_order") or edits.get("unmapped"))


# ── nodes ────────────────────────────────────────────────────────────────────

def node_tex(user_id, node: Dict[str, Any]) -> str:
    """The source a node stands for: its hand-edited `.tex`, else what the formatter makes of its
    content with the node's layout override laid over it (the executor does the same, #277)."""
    if node.get("edited_tex"):
        return node["edited_tex"]
    from harness import render
    from harness.executor import _apply_layout

    content = _apply_layout(node.get("layout_overrides"), copy.deepcopy(node.get("content") or {}))
    return render.generated_tex(user_id, content)


def node_edits(user_id, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """The tex edits from node `a` to node `b` (full nodes). Falls back to one unmapped region when
    the earlier source cannot be produced, so a tex change is never reported as nothing."""
    try:
        return compare(node_tex(user_id, a), node_tex(user_id, b))
    except Exception as exc:  # noqa: BLE001 — a report must not fail on a renderer problem
        return {"bullets": [], "reordered": [], "section_order": None, "unmapped": [{
            "region": "document", "diff": "",
            "reason": f"the .tex changed, but the versions could not be compared ({exc})"}]}


# ── what a new version would drop ────────────────────────────────────────────

def _find(content: Dict, key: str) -> Optional[Dict]:
    for section, keyer in (("experiences", exp_key), ("projects", proj_key)):
        for it in content.get(section) or []:
            if keyer(it) == key:
                return it
    return None


def uncarried(edits: Dict[str, Any], content: Dict, section_order: Optional[List[str]] = None
              ) -> Dict[str, Any]:
    """The part of `edits` that `content` does not already hold: a revised or added bullet whose
    text is not on the item, a removed one that still is, bullets still in another order, and
    every unmapped region (a plan cannot carry those). What is left is what a commit would drop."""
    left: Dict[str, Any] = {"bullets": [], "reordered": [], "section_order": None, "unmapped": []}
    for e in edits.get("bullets") or []:
        item = _find(content, e["item"]) if e["item"].startswith(CARRYABLE) else None
        texts = [norm(t) for t in (item or {}).get("bullets") or []]
        if e["change"] == "removed":
            # Gone from the item, or the whole item dropped; an item no plan can address is neither.
            carried = e["item"].startswith(CARRYABLE) and (
                item is None or norm(e["before"]) not in texts)
        else:
            carried = item is not None and norm(e["after"]) in texts
        if not carried:
            left["bullets"].append(e)
    for r in edits.get("reordered") or []:
        item = _find(content, r["item"]) if r["item"].startswith(CARRYABLE) else None
        want = [norm(t) for t in r["order"]]
        have = [norm(t) for t in (item or {}).get("bullets") or []]
        if item is None or [t for t in have if t in want] != [t for t in want if t in have]:
            left["reordered"].append(r)
    order = edits.get("section_order")
    if order:
        have = [s for s in (section_order or content.get("_section_order") or []) if s in order["after"]]
        want = [s for s in order["after"] if s in have]
        if have != want:
            left["section_order"] = order
    left["unmapped"] = list(edits.get("unmapped") or [])
    return left


# ── a short form for get_head and the prompt hook ────────────────────────────

def brief(edits: Optional[Dict[str, Any]], max_bullets: int = 8, max_chars: int = 160,
          max_diff_lines: int = 12) -> Optional[Dict[str, Any]]:
    """`edits` cut down for an event feed: the first few bullets, texts clipped, diffs shortened,
    with `truncated` counting what was left out."""
    if edits is None:
        return None

    def clip(text):
        return text if text is None or len(text) <= max_chars else text[:max_chars - 1] + "…"

    bullets = [{**e, "before": clip(e["before"]), "after": clip(e["after"])}
               for e in (edits.get("bullets") or [])[:max_bullets]]
    unmapped = []
    for r in edits.get("unmapped") or []:
        diff = r.get("diff", "").split("\n")
        if len(diff) > max_diff_lines:
            diff = diff[:max_diff_lines] + [f"... ({len(diff) - max_diff_lines} more lines)"]
        unmapped.append({**r, "diff": "\n".join(diff)})
    out = {"bullets": bullets,
           "reordered": [{k: v for k, v in r.items() if k != "order"}
                         for r in edits.get("reordered") or []],
           "section_order": edits.get("section_order"), "unmapped": unmapped}
    cut = max(len(edits.get("bullets") or []) - max_bullets, 0)
    if cut:
        out["truncated"] = cut
    return out


def public(edits: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """`edits` as `diff_nodes` returns them: nothing cut, the internal `order` of reordered items
    dropped."""
    return brief(edits, 10 ** 9, 10 ** 9, 10 ** 9)
