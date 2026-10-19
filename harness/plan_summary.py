"""The plan summary an `execute_plan` dry run returns (issue #392).

The tailoring plan is the one point where the user approves, so the host shows them, before it
commits, what will change and why. A dry run carries `plan_summary` beside `active_constraints`
(#299): one entry per node, in page order, with the item's key and title, the change in plain
words (kept / revised with N bullets / replaced X with Y / dropped / cut bullets #b...), the
reason (`because`, required on every new node) and what the dry run made of it, then the skills
line. `text` is the same as a few lines the host shows the user verbatim.

Additive: built from what the executor already computed, it changes no other field, and a
commit does not carry it.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from agents.checks import exp_key, proj_key

TITLE = "Plan for this job"

# A dry-run status that means the node would not change the page as planned.
_NOT_APPLIED = ("reverted", "refused")


def _title(item: Optional[Dict[str, Any]], key: str) -> str:
    """An item as the user reads it: "Data Scientist, Acme Analytics" or the project's name."""
    if item:
        if key.startswith("exp:"):
            parts = [str(item.get(f) or "").strip() for f in ("title", "company")]
            if any(parts):
                return ", ".join(p for p in parts if p)
        name = str(item.get("name") or "").strip()
        if name:
            return name
    return key


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def reason_words(because: Optional[str], pref_texts: Dict[str, str]) -> str:
    """`because` in plain words. A `pref:` reason names the preference by its own text."""
    because = (because or "").strip()
    if not because:
        return "no reason given (a program saved before reasons were required)"
    prefix, _, rest = because.partition(":")
    rest = rest.strip()
    if prefix == "user":
        return f"you asked: {rest}"
    if prefix == "job":
        return f"the posting: {rest}"
    if prefix == "pref":
        text = pref_texts.get(rest.lower())
        return f'your preference "{" ".join(text.split())}"' if text else f"your preference {rest}"
    return because


def build(*, nodes: List[Dict[str, Any]], results: List[Dict[str, Any]], base: Dict[str, Any],
          final: Dict[str, Any], kg_item: Callable[[str], Optional[Dict[str, Any]]],
          pref_texts: Dict[str, str], skills_planned: bool, has_parent: bool,
          skill_notes: Dict[str, List[str]], job_title: str = "",
          job_company: str = "") -> Dict[str, Any]:
    """The summary. `nodes` are the program's nodes in the order they ran, `results` the
    executor's row for each, in the same order. `base` is the parent version (what
    `delete_bullet` ids and the "before" titles read), `final` the page the dry run would
    commit. `kg_item(key)` is the knowledge-graph item for a key (None when the graph has no
    such item), for items not on the base."""

    def item_of(key: str) -> Optional[Dict[str, Any]]:
        key = key.strip().lower()
        for section in ("experiences", "projects"):
            for item in base.get(section) or []:
                if (exp_key(item) if section == "experiences" else proj_key(item)) == key:
                    return item
        return kg_item(key)

    entries: List[Dict[str, Any]] = []
    for node, row in zip(nodes, results):
        op, key = node["op"], node["item_key"]
        item = item_of(key)
        entry: Dict[str, Any] = {
            "id": node["id"], "op": op, "item_key": key, "title": _title(item, key),
            "because": node.get("because") or None,
            "reason": reason_words(node.get("because"), pref_texts),
            "status": row.get("status"),
        }
        bullets = [b["text"] for b in node.get("bullets") or []]
        if op == "keep":
            change = "kept as is"
        elif op == "revise":
            change = f"revised with {_plural(len(bullets), 'bullet')}"
            if node.get("keywords"):
                change += f", weaving in {', '.join(node['keywords'])}"
        elif op == "replace":
            rep = node.get("replacement_key") or ""
            rep_title = _title(kg_item(rep.strip().lower()), rep)
            entry.update(replacement_key=rep, replacement_title=rep_title)
            change = f"replaced {entry['title']} with {rep_title}"
            if bullets:
                change += f" ({_plural(len(bullets), 'bullet')})"
        elif op == "delete":
            change = "dropped"
        else:  # delete_bullet
            source = (item or {}).get("bullets") or []
            cut = []
            for ref in node.get("cut_bullets") or []:
                idx = ref.strip().lower().rpartition("#b")[2]
                n = int(idx) if idx.isdigit() else -1
                cut.append({"bullet": ref, "text": source[n] if 0 <= n < len(source) else None})
            entry["cut"] = cut
            change = f"cut {_plural(len(cut), 'bullet')} " + ", ".join(
                "#" + c["bullet"].rpartition("#")[2] for c in cut)
        entry["change"] = change
        if bullets:
            entry["bullets"] = bullets
        if row.get("status") in _NOT_APPLIED:
            entry["not_applied"] = row.get("reason")
        entries.append(entry)

    line = [s.get("name") for s in final.get("skills_ranked") or [] if s.get("name")]
    skills = {"line": line,
              "source": "plan" if skills_planned else ("current" if has_parent else "ranked"),
              "unknown": list(skill_notes.get("unknown") or []),
              "suppressed": list(skill_notes.get("suppressed") or [])}
    title = TITLE
    if job_title.strip():
        title = f"Plan for {job_title.strip()}" + (f" at {job_company.strip()}"
                                                   if job_company.strip() else "")
    summary = {"title": title, "nodes": entries, "skills": skills}
    summary["text"] = render_text(summary)
    return summary


def _quote(text: Any) -> str:
    return '"' + " ".join(str(text or "").split()) + '"'


def render_text(summary: Dict[str, Any]) -> str:
    """The summary as the lines the host shows the user, word for word."""
    lines = [f"{summary['title']} (dry run: nothing is saved until you approve)"]
    if not summary["nodes"]:
        lines.append("No changes to experiences or projects.")
    for n, entry in enumerate(summary["nodes"], 1):
        lines.append(f"{n}. {entry['title']}: {entry['change']}")
        lines.append(f"   Why: {entry['reason']}")
        for bullet in entry.get("bullets") or []:
            lines.append(f"   - {' '.join(bullet.split())}")
        for cut in entry.get("cut") or []:
            text = _quote(cut["text"]) if cut.get("text") else "(no such bullet)"
            lines.append(f"   - cut #{cut['bullet'].rpartition('#')[2]}: {text}")
        if entry.get("not_applied"):
            lines.append(f"   ART would not apply this ({entry['status']}): {entry['not_applied']}")
    skills = summary["skills"]
    label = {"plan": "Skills", "current": "Skills (unchanged)",
             "ranked": "Skills (ranked for this posting)"}[skills["source"]]
    lines.append(f"{label}: {', '.join(skills['line']) if skills['line'] else '(none)'}")
    if skills["unknown"]:
        lines.append(f"   Not in your profile, left off: {', '.join(skills['unknown'])}")
    if skills["suppressed"]:
        lines.append(f"   Left off by your preferences: {', '.join(skills['suppressed'])}")
    return "\n".join(lines)
