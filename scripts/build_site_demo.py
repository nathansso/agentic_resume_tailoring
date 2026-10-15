#!/usr/bin/env python
"""Rebuild the landing site's demo from a real ART run (issues #275, #337).

    python scripts/build_site_demo.py --tectonic C:\tools\tectonic.exe     # capture + media
    python scripts/build_site_demo.py --media-only DIR   # re-render images from kept PDFs

What it does, in order:

1. Creates a scratch `ART_DATA_DIR` (a new temp directory, never `~/.art`), strips every
   database, key and `ART_*` variable from the environment, and turns Jev off.
2. Drives the real `art` tools through `python -m harness.entry <tool> --args <json>`, the
   same entry point the `art` script runs, in the order `/art:setup` and `/art:tailor` take:
   a sample resume and the READMEs of three repos that are not on it go into the graph, a
   sample posting is opened, version 1 is the student's usual one-page resume (the resume's
   content, the repo projects left off, nothing reworded), and the tailoring plan is run
   through the gates to version 3. The "user" and "agent" lines and the agent's records,
   requirements and plan are in `scripts/site_demo/demo_script.py`; everything ART answers
   is captured as it comes back.
3. Writes `site/src/components/demo/transcript.json`, the replay's only data.
4. Renders version 1 and version 3 to PDF (tectonic or pdflatex), rasterises each whole
   page to WebP under `site/public/demo/`, finds the lines that differ by diffing the PDFs'
   own text, groups the changes from the two versions' contents and ART's `diff_nodes`, and
   writes `site/src/components/demo/before-after.json`. It stops if either page ends above
   the bottom tenth of the page.

Nothing in the transcript or the images is edited by hand. The display form of a long tool
result drops fields and shortens lists, and says so in place (`"…"`); the full answers are
not kept. Playback pacing is a fixed rate, not the call's real duration.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "site_demo"))

import demo_script as S  # noqa: E402  (the hand-written side of the demo)

SITE = ROOT / "site"
DEMO_SRC = SITE / "src" / "components" / "demo"
TRANSCRIPT = DEMO_SRC / "transcript.json"
BEFORE_AFTER = DEMO_SRC / "before-after.json"
IMG_DIR = SITE / "public" / "demo"
RESUME_MD = Path(__file__).resolve().parent / "site_demo" / "sample_resume.md"
POSTING_TXT = Path(__file__).resolve().parent / "site_demo" / "sample_posting.txt"

LABEL = "Replay of a real ART run on a sample student profile"
PHASES = [
    {"id": "setup", "label": "Set up"},
    {"id": "posting", "label": "Posting"},
    {"id": "plan", "label": "Plan"},
    {"id": "approve", "label": "Approval"},
    {"id": "gates", "label": "Gate check"},
    {"id": "render", "label": "Render"},
]

# Playback: milliseconds a step stays on screen, from its length. Fixed rates, not timings.
MS_USER_BASE, MS_USER_CHAR = 600, 30
MS_AGENT_BASE, MS_AGENT_CHAR = 400, 11
MS_TOOL_BASE, MS_TOOL_LINE = 700, 45
PAGE_W_PX = (1100, 600)          # the two image widths written
MIN_FILL = 0.90                  # a page's last line of text must end in the bottom tenth

SCRATCH_PREFIXES = ("DATABASE_URL", "TYPESAFE", "ART_", "OPENAI", "ANTHROPIC", "SUPABASE",
                    "LANGCHAIN", "LANGSMITH", "GITHUB", "GH_", "UV_", "VIRTUAL_ENV")


# --- running ART ------------------------------------------------------------------------

def clean_env(data_dir: Path, engine_dir: Path | None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(SCRATCH_PREFIXES)}
    env.update(ART_DATA_DIR=str(data_dir), ART_JEV_MODE="off", PYTHON_DOTENV_DISABLED="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if engine_dir:
        env["PATH"] = str(engine_dir) + os.pathsep + env.get("PATH", "")
    return env


class Art:
    """Calls the real tools through the `art` entry point, in a scratch data directory."""

    def __init__(self, data_dir: Path, env: dict):
        self.data_dir, self.env, self.raw = data_dir, env, []

    def __call__(self, tool: str, args: dict | None = None) -> dict:
        args = args or {}
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "harness.entry", tool, "--args", json.dumps(args)],
            capture_output=True, text=True, encoding="utf-8", env=self.env, cwd=ROOT)
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            raise SystemExit(f"`art {tool}` printed no JSON (exit {proc.returncode}):\n"
                             f"{proc.stdout[-600:]}\n{proc.stderr[-1200:]}")
        self.raw.append({"tool": tool, "args": args, "result": result,
                         "seconds": round(time.monotonic() - started, 2)})
        return result


def find_engine(explicit: str | None) -> tuple[Path | None, str]:
    """Directory holding tectonic/pdflatex, and its name. `--tectonic` wins, then PATH."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"--tectonic {explicit}: no such file")
        return path.parent, path.name
    for name in ("tectonic", "pdflatex"):
        found = shutil.which(name)
        if found:
            return None, name
    raise SystemExit("No LaTeX engine: pass --tectonic <path to tectonic>, or put tectonic or "
                     "pdflatex on PATH. The demo's PDFs are never faked.")


# --- the display form of a tool's input and output --------------------------------------

def shrink(obj, *, items: int = 4, chars: int = 150):
    """Shorter lists and strings, marked in place with an ellipsis. Never rewrites a value."""
    if isinstance(obj, dict):
        return {k: shrink(v, items=items, chars=chars) for k, v in obj.items()}
    if isinstance(obj, list):
        head = [shrink(x, items=items, chars=chars) for x in obj[:items]]
        if len(obj) > items:
            head.append(f"… {len(obj) - items} more")
        return head
    if isinstance(obj, str) and len(obj) > chars:
        return obj[: chars - 1].rstrip() + "…"
    return obj


def pick(obj: dict, keys: list[str]) -> dict:
    """Only these fields, then one line saying how many were left out."""
    out = {k: obj[k] for k in keys if k in obj}
    rest = len(obj) - len(out)
    if rest:
        out["…"] = f"{rest} more fields"
    return out


NODE_KEYS = ["id", "op", "item_key", "status", "reason", "improved"]


def slim_plan_result(result: dict, keys: list[str], node_keys: list[str] = NODE_KEYS) -> dict:
    """An execute_plan answer: the verdict fields, and per node only the ones that say what
    happened. Every cut is marked (`"…": "N more fields"`), at the top and on each node."""
    out = pick(result, keys)
    if "nodes" in out:
        out["nodes"] = [pick(n, node_keys) for n in result["nodes"]]
    return out


def slim_program(program: dict, show_bullets_of: str) -> dict:
    """A plan as the replay shows it: each node's id, op and target, with its bullets counted
    (`"… 4 bullets"`) rather than listed, except `show_bullets_of`, whose draft is the point."""
    nodes = []
    for n in program["nodes"]:
        d = {k: n[k] for k in ("id", "op", "item_key", "replacement_key", "because") if k in n}
        bullets = n.get("bullets")
        if bullets and n["id"] == show_bullets_of:
            d["bullets"] = [bullets[1], f"… {len(bullets) - 1} more bullets"]
        elif bullets:
            d["bullets"] = f"… {len(bullets)} bullets, each citing its source"
        if len(n) > len(d):
            d["…"] = f"{len(n) - len(d)} more fields"
        nodes.append(d)
    skills = program.get("skills") or []
    return {"program": {"job_id": program["job_id"], "parent": program["parent"], "nodes": nodes,
                        "skills": skills[:3] + [f"… {len(skills) - 3} more"] if len(skills) > 3 else skills,
                        **({"section_order": program["section_order"]} if program.get("section_order") else {}),
                        "host": program.get("host")}}


def sanitize(obj, data_dir: Path):
    """The scratch data directory is `~/.art` as far as the page is concerned."""
    forms = {str(data_dir), str(data_dir).replace("\\", "/")}

    def fix(text: str) -> str:
        for form in forms:
            text = text.replace(form, "~/.art")
        return text.replace("\\", "/") if "~/.art" in text else text

    if isinstance(obj, dict):
        return {k: sanitize(v, data_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(x, data_dir) for x in obj]
    return fix(obj) if isinstance(obj, str) else obj


def shown_lines(*docs) -> int:
    return sum(len(json.dumps(d, indent=2, ensure_ascii=False).splitlines()) for d in docs if d)


# --- the run ----------------------------------------------------------------------------

def capture(art: Art, tex_dir: Path) -> tuple[list[dict], dict]:
    events: list[dict] = []
    keep: dict = {}

    def say(phase, type_, text, **extra):
        events.append({"phase": phase, "type": type_, "text": text, **extra})

    def tool(phase, name, args, *, show_args=None, show_result=None, items=4, chars=150):
        result = art(name, args)
        assert not result.get("error"), f"{name} failed: {result.get('error')}"
        sa = shrink(args if show_args is None else show_args, items=items, chars=chars)
        sr = shrink(result if show_result is None else show_result(result), items=items, chars=chars)
        events.append({"phase": phase, "type": "tool", "name": name,
                       "args": sanitize(sa, art.data_dir), "result": sanitize(sr, art.data_dir)})
        return result

    def skill_records(skills):
        return [{"kind": "skill", "data": {"name": n, "category": c, "proficiency": 3}}
                for n, c in skills]

    # -- set up: the resume, then repos that are not on it
    say("setup", "user", S.USER_SETUP)
    say("setup", "agent", S.AGENT_SETUP_1)
    resume_records = list(S.RESUME_RECORDS) + skill_records(S.RESUME_SKILLS)
    r = tool("setup", "upsert_items", {"records": resume_records, "source": "resume.md"},
             show_result=lambda r: pick(r, ["counts", "results"]), items=2, chars=100)
    assert r["counts"].get("created", 0) == len(resume_records), r["counts"]
    say("setup", "agent", S.AGENT_SETUP_2.format(created=r["counts"]["created"]))
    repo_records = list(S.REPO_RECORDS) + skill_records(S.REPO_SKILLS)
    r = tool("setup", "upsert_items", {"records": repo_records, "source": "github READMEs"},
             show_result=lambda r: pick(r, ["counts", "results"]), items=2, chars=100)
    assert r["counts"].get("created", 0) == len(repo_records), r["counts"]
    say("setup", "agent", S.AGENT_SETUP_3.format(created=r["counts"]["created"]))
    tool("setup", "update_profile", {"fields": S.PROFILE})
    listed = tool("setup", "list_items", {}, items=3, chars=110)
    kinds = [i["kind"] for i in listed["items"]]
    keys = {i["key"] for i in listed["items"]}
    for needed in (S.KESTREL, S.TA, S.FERNHOLLOW, S.RECYCLING, S.BUDGET, S.PORTFOLIO, S.DINING,
                   S.ABTEST, S.CHURN, S.RETAIL):
        assert needed in keys, f"{needed} is not a key ART returned: {sorted(keys)}"
    say("setup", "agent", S.AGENT_SETUP_4.format(
        n_exp=kinds.count("experience"), n_proj=kinds.count("project"), n_skill=kinds.count("skill")))

    # -- posting
    posting = POSTING_TXT.read_text(encoding="utf-8")
    say("posting", "user", S.USER_TAILOR_TEMPLATE.format(
        title=S.POSTING_TITLE, company=S.POSTING_COMPANY, lines=len(posting.splitlines())))
    say("posting", "agent", S.AGENT_POSTING_1)
    tool("posting", "art_briefing", {"role_family": S.ROLE_FAMILY},
         show_result=lambda r: pick(r, ["pins", "preferences", "counts", "role_family"]))
    say("posting", "agent", S.AGENT_POSTING_2.format(n_req=len(S.REQUIREMENTS)))
    art_requirements = [{k: v for k, v in q.items() if k not in ("id", "short")}
                        for q in S.REQUIREMENTS]
    opened = tool("posting", "open_job",
                  {"jd_text": posting, "requirements": art_requirements,
                   "metadata": {"title": S.POSTING_TITLE, "company": S.POSTING_COMPANY,
                                "role_family": S.ROLE_FAMILY}},
                  show_result=lambda r: pick(r, ["job_id", "title", "company", "requirements",
                                                 "role_family", "skill_matches", "unmatched_terms"]),
                  items=3, chars=110)
    job_id = opened["job_id"]
    say("posting", "agent", S.AGENT_POSTING_3.format(n_match=len(opened["skill_matches"])))

    # -- version 1: the usual resume, from the same graph, nothing reworded
    say("posting", "agent", S.AGENT_BASE)
    usual = {"job_id": job_id, "parent": None,
             "nodes": [{"id": f"d{i}", "op": "delete", "item_key": key, "because": S.LEAVE_OFF_REASON}
                       for i, key in enumerate((S.ABTEST, S.CHURN, S.RETAIL), 1)],
             "skills": S.GENERIC_SKILLS}
    base = tool("posting", "execute_plan", {"program": usual},
                show_result=lambda r: {"committed": r["committed"], "node_id": r["node_id"],
                                       "line_budget": r["line_budget"], "violations": r["violations"],
                                       "metrics": {"final": {"gates": r["metrics"]["final"]["gates"]},
                                                   "…": "guards, targets and the ATS-style report left out"}})
    assert base["committed"], base
    base_node = base["node_id"]
    before_content = art("get_head", {"job_id": job_id})["head"]["content"]
    say("posting", "agent", S.AGENT_READ)
    exp = tool("posting", "get_item", {"key": S.KESTREL}, chars=120)

    # Source bullets of every item, as ART lists them (evidence ids are `<key>#b<n>`).
    sources: dict[str, list[str]] = {}
    for key in sorted(keys):
        if key.startswith(("exp:", "proj:")):
            rec = (exp if key == S.KESTREL else art("get_item", {"key": key}))["record"]
            sources[key] = ([b["content"] for b in rec["blurbs"]] if key.startswith("proj:")
                            else list(rec["bullets"]))

    # -- plan
    tool("plan", "suggest_actions", {"job_id": job_id}, items=2, chars=100)
    events.append({"phase": "plan", "type": "agent", "text": S.AGENT_PLAN_INTRO,
                   "items": S.AGENT_PLAN_ITEMS, "outro": S.AGENT_PLAN_OUTRO, "note": S.SLIP_NOTE})

    # -- approval
    say("approve", "user", S.USER_APPROVE)

    # -- gate check
    def evidence(item_key: str, start_of_source: str) -> str:
        for i, text in enumerate(sources[item_key]):
            if text.startswith(start_of_source):
                return f"{item_key}#b{i}"
        raise SystemExit(f"{item_key}: no source bullet starts with {start_of_source!r}")

    def bullets(item_key, pairs):
        out = []
        for text, starts in pairs:
            starts = [starts] if isinstance(starts, str) else starts
            out.append({"text": text, "cites": [evidence(item_key, s) for s in starts]})
        return out

    staged_ab = [(t.replace(S.AB_RESULT, S.AB_RESULT_SLIP), s) for t, s in S.AB_BULLETS]
    assert staged_ab != S.AB_BULLETS and S.SLIP_FIGURE in staged_ab[1][0]

    def node(nid, op, item_key, bullet_pairs=None, **extra):
        n = {"id": nid, "op": op, "item_key": item_key, **extra}
        if bullet_pairs is not None:
            target = extra.get("replacement_key", item_key)
            n["bullets"] = bullets(target, bullet_pairs)
        if nid in S.NODE_KEYWORDS:
            n["keywords"] = S.NODE_KEYWORDS[nid]
        return n

    def plan_nodes(ab_pairs):
        return [
            node("n1", "revise", S.KESTREL, S.KESTREL_BULLETS, strategy="keyword_weave"),
            node("n2", "revise", S.TA, S.TA_BULLETS, strategy="keyword_weave"),
            node("n3", "replace", S.RECYCLING, ab_pairs, replacement_key=S.ABTEST),
            node("n4", "replace", S.BUDGET, S.CHURN_BULLETS, replacement_key=S.CHURN),
            node("n5", "keep", S.DINING),
            node("n6", "replace", S.PORTFOLIO, S.RETAIL_BULLETS, replacement_key=S.RETAIL),
            node("n7", "delete", S.FERNHOLLOW, because=S.DROP_REASON),
        ]

    program = {"job_id": job_id, "parent": base_node, "nodes": plan_nodes(staged_ab),
               "skills": S.SKILL_ORDER, "section_order": S.SECTION_ORDER,
               "host": {"name": "claude-code"}}
    say("gates", "agent", S.AGENT_GATES_1)
    first = art("execute_plan", {"program": program})
    assert not first.get("error"), first
    status = {n["id"]: n for n in first["nodes"]}
    assert status["n3"]["status"] == "reverted", status["n3"]
    assert "consistency" in json.dumps(status["n3"]) and S.SLIP_FIGURE in json.dumps(status["n3"]), status["n3"]
    for nid in ("n1", "n2", "n4", "n6", "n7"):
        assert status[nid]["status"] == "accepted", status[nid]
    assert status["n5"]["status"] == "kept", status["n5"]
    assert first["committed"], "the other nodes should still commit"
    events.append({
        "phase": "gates", "type": "tool", "name": "execute_plan",
        "args": sanitize(shrink(slim_program(program, "n3"), items=8, chars=330), art.data_dir),
        "result": sanitize(shrink(slim_plan_result(first, ["committed", "node_id", "nodes", "violations"],
                                                   ["id", "op", "item_key", "status", "reason"]),
                                  items=8, chars=230), art.data_dir)})
    say("gates", "agent", S.AGENT_GATES_2)
    v2 = first["node_id"]
    fixed = [n for n in plan_nodes(S.AB_BULLETS) if n["id"] == "n3"]
    assert S.SLIP_FIGURE not in json.dumps(fixed)
    second = tool("gates", "execute_plan",
                  {"program": {"job_id": job_id, "parent": v2, "nodes": fixed}},
                  show_result=lambda r: slim_plan_result(r, ["committed", "node_id", "parent", "nodes", "violations"]),
                  items=8, chars=230)
    assert second["committed"], second
    assert second["nodes"][0]["status"] == "accepted", second["nodes"][0]
    after_node = second["node_id"]
    say("gates", "agent", S.AGENT_GATES_3)
    diff = tool("gates", "diff_nodes", {"from_node": base_node, "to_node": after_node},
                show_result=lambda r: pick(r, ["items", "skills_added", "skills_removed"]),
                items=3, chars=96)
    after_content = art("get_head", {"job_id": job_id})["head"]["content"]
    say("gates", "agent", S.AGENT_GATES_4)

    # -- render
    out = {}
    for label, node_id in (("before", base_node), ("after", after_node)):
        rendered = tool("render", "render", {"job_id": job_id, "node_id": node_id},
                        show_result=lambda r: pick(r, ["node_id", "pdf_path", "pages", "source"]))
        assert rendered["pdf_path"], rendered.get("hint")
        pdf = tex_dir / f"{label}.pdf"
        shutil.copyfile(rendered["pdf_path"], pdf)
        (tex_dir / f"{label}.tex").write_text(Path(rendered["tex_path"]).read_text(encoding="utf-8"),
                                              encoding="utf-8")
        out[label] = {"pdf": pdf, "pages": rendered["pages"], "shown_path": rendered["pdf_path"]}
    assert out["before"]["pages"] == 1, f"the usual resume is {out['before']['pages']} pages"
    assert out["after"]["pages"] == 1, f"tailored resume is {out['after']['pages']} pages"
    say("render", "agent", S.AGENT_DONE.format(pdf=sanitize(out["after"]["shown_path"], art.data_dir)))
    keep.update(job_id=job_id, base_node=base_node, after_node=after_node, files=out,
                groups_input={"before": before_content, "after": after_content, "diff": diff,
                              "sources": sources, "nodes": program["nodes"]})
    return events, keep


def duration_ms(event: dict) -> int:
    if event["type"] == "user":
        return MS_USER_BASE + MS_USER_CHAR * len(event["text"])
    if event["type"] == "agent":
        text = " ".join([event["text"], *event.get("items", []), event.get("outro", ""),
                         event.get("note", "")])
        return MS_AGENT_BASE + MS_AGENT_CHAR * len(text)
    return MS_TOOL_BASE + MS_TOOL_LINE * shown_lines(event["args"], event["result"])


# --- the before/after media ------------------------------------------------------------

def pdf_lines(pdf_path: Path):
    """Text lines of page 1 with boxes as page fractions (top-left origin).

    pdfium hands back one rectangle per run of text; runs on the same baseline (a bullet
    glyph, a heading and its date) are joined into one line.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[0]
    width, height = page.get_size()
    tp = page.get_textpage()
    runs = []
    for i in range(tp.count_rects()):
        left, bottom, right, top = tp.get_rect(i)
        text = tp.get_text_bounded(left, bottom, right, top)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            runs.append({"l": left, "b": bottom, "r": right, "t": top, "text": text})
    rows: list[list[dict]] = []
    for run in sorted(runs, key=lambda r: -(r["t"] + r["b"])):
        mid = (run["t"] + run["b"]) / 2
        for row in rows:
            ref = row[0]
            if abs((ref["t"] + ref["b"]) / 2 - mid) < 0.45 * max(ref["t"] - ref["b"], run["t"] - run["b"]):
                row.append(run)
                break
        else:
            rows.append([run])
    lines = []
    for row in rows:
        row.sort(key=lambda r: r["l"])
        l, r_ = min(x["l"] for x in row), max(x["r"] for x in row)
        b, t = min(x["b"] for x in row), max(x["t"] for x in row)
        joined = re.sub(r"\s+([:,.;])", r"\1", " ".join(x["text"] for x in row))
        lines.append({"text": joined, "x": l / width, "y": 1 - t / height,
                      "w": (r_ - l) / width, "h": (t - b) / height})
    doc.close()
    return sorted(lines, key=lambda ln: ln["y"])


BULLET = re.compile(r"^[•◦▪·]\s*")


def group_items(lines: list[dict]) -> list[dict]:
    """Join a bullet's wrapped lines so a change is judged, and highlighted, per bullet."""
    items: list[dict] = []
    for ln in lines:
        prev = items[-1] if items else None
        bullet = prev is not None and BULLET.match(prev["lines"][0]["text"]) is not None
        wrapped = (prev is not None and bullet and not BULLET.match(ln["text"])
                   and ln["y"] - prev["lines"][-1]["y"] < 2.2 * ln["h"]
                   and ln["x"] > prev["lines"][0]["x"] + 0.008)
        if wrapped:
            prev["lines"].append(ln)
        else:
            items.append({"lines": [ln]})
    for it in items:
        it["text"] = BULLET.sub("", " ".join(l["text"] for l in it["lines"]))
    return items


def rect(lines: list[dict]) -> dict:
    x0 = min(l["x"] for l in lines)
    y0 = min(l["y"] for l in lines)
    x1 = max(l["x"] + l["w"] for l in lines)
    y1 = max(l["y"] + l["h"] for l in lines)
    pad = 0.002
    return {"x": round(x0 - pad, 4), "y": round(y0 - pad, 4),
            "w": round(x1 - x0 + 2 * pad, 4), "h": round(y1 - y0 + 2 * pad, 4)}


# --- what changed, grouped, from the two versions ------------------------------------------

def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _title(key: str, item: dict) -> str:
    if key.startswith("exp:"):
        return f"{item.get('title', '')}, {item.get('company', '')}".strip(", ")
    return item.get("name", "")


def _index(content: dict) -> dict:
    """item key -> {section, pos, n, item} for every experience and project on a version."""
    from agents.checks import exp_key, proj_key

    out = {}
    for section, fn in (("experiences", exp_key), ("projects", proj_key)):
        items = content.get(section) or []
        for pos, item in enumerate(items):
            out[fn(item)] = {"section": section, "pos": pos, "n": len(items), "item": item}
    return out


def _source_of(item: dict, text: str, sources: dict) -> str | None:
    """The text of the first source bullet a page bullet cites, or None when it cites none."""
    cites = (item.get("cites") or {}).get(text) or []
    for cite in cites:
        key, _, idx = cite.rpartition("#b")
        if idx.isdigit() and int(idx) < len(sources.get(key, [])):
            return sources[key][int(idx)]
    return None


def build_groups(inp: dict, requirements: list[dict], cut_reasons: dict) -> list[dict]:
    """The changes between two versions, grouped by kind and each tied to the posting
    requirement it serves.

    `inp` carries the two versions' contents as ART stores them (`get_head`), ART's own
    `diff_nodes` answer for the pair, every item's source bullets (`get_item`) and the plan's
    nodes. Everything here is read from those. Two things are not: which requirement a change
    serves is the posting terms the plan named for that item (`keywords`) matched against the
    terms each requirement lists, and the reason for a cut is `cut_reasons`, written for the
    demo.
    """
    before, after = _index(inp["before"]), _index(inp["after"])
    sources, diff = inp["sources"], inp["diff"]
    nodes = inp["nodes"]
    keywords: dict[str, set] = {}
    replaces: dict[str, str] = {}
    for n in nodes:
        target = (n.get("replacement_key") or n["item_key"]).lower()
        keywords[target] = {k.lower() for k in n.get("keywords") or []}
        if n["op"] == "replace":
            replaces[target] = n["item_key"].lower()
    replaced_by = {v: k for k, v in replaces.items()}

    def serves(key: str, text: str | None = None) -> list[dict]:
        wanted = keywords.get(key, set())
        hits = [r for r in requirements if wanted & {t.lower() for t in r["terms"]}]
        hits.sort(key=lambda r: -r["criticality"])
        if text is not None:
            low = text.lower().replace("-", " ")
            narrowed = [r for r in hits if any(t.lower().replace("-", " ") in low for t in r["terms"])]
            hits = narrowed or hits[:1]
        return [{"id": r["id"], "short": r["short"]} for r in hits[:2]]

    added, moved, reworded, cut = [], [], [], []
    ordinals = ["first", "second", "third", "fourth", "fifth"]

    for key, a in after.items():
        item = a["item"]
        title = _title(key, item)
        texts = [b for b in item.get("bullets") or [] if (b or "").strip()]
        b_side = before.get(key)
        if b_side is None:
            added.append({
                "key": key, "title": title, "kind": "project" if key.startswith("proj:") else "role",
                "position": a["pos"] + 1, "ordinal": ordinals[a["pos"]] if a["pos"] < len(ordinals) else str(a["pos"] + 1),
                "of": a["n"], "bullets": texts,
                "replaces": _title(replaces[key], before[replaces[key]]["item"]) if replaces.get(key) in before else None,
                "serves": serves(key)})
        else:
            old_texts = [t for t in b_side["item"].get("bullets") or [] if (t or "").strip()]
            old_norm = [_norm(t) for t in old_texts]
            # rank among the bullets present on both pages, so a move up is not an artefact of a cut
            shared = []
            for pos, text in enumerate(texts):
                prior = _norm(_source_of(item, text, sources) or text)
                if prior in old_norm:
                    shared.append((pos, prior, text))
            by_old = sorted(shared, key=lambda s: old_norm.index(s[1]))
            old_rank = {s[1]: r for r, s in enumerate(by_old)}
            for new_rank, (pos, prior, text) in enumerate(shared):
                if new_rank < old_rank[prior]:
                    moved.append({"scope": "bullet", "key": key, "title": title, "text": text,
                                  "from": old_norm.index(prior) + 1, "to": pos + 1,
                                  "serves": serves(key, text)})
            kept_priors = {s[1] for s in shared}
            for text in old_texts:
                if _norm(text) not in kept_priors:
                    cut.append({"key": key, "title": title, "scope": "bullet", "text": text,
                                "reason": cut_reasons.get(key) or cut_reasons["default"]})
        for text in texts:
            src = _source_of(item, text, sources)
            if src and _norm(src) != _norm(text):
                reworded.append({"key": key, "title": title, "before": src, "after": text,
                                 "from_graph": b_side is None or _norm(src) not in
                                 [_norm(t) for t in b_side["item"].get("bullets") or []],
                                 "serves": serves(key, text)})
    for key, b in before.items():
        if key not in after:
            new = replaced_by.get(key)
            cut.append({"key": key, "title": _title(key, b["item"]), "scope": "item",
                        "n_bullets": len(b["item"].get("bullets") or []),
                        "replaced_by": _title(new, after[new]["item"]) if new in after else None,
                        "reason": cut_reasons.get(key) or cut_reasons["default"]})

    # ART's own diff must agree with what was read off the two pages.
    diff_items = {i["key"]: i for i in diff.get("items") or []}
    assert ({k for k, i in diff_items.items() if i["change"] == "added"}
            == {e["key"] for e in added if e.get("kind")}), "diff_nodes and the pages disagree on added items"
    assert ({k for k, i in diff_items.items() if i["change"] == "removed"}
            == {e["key"] for e in cut if e["scope"] == "item"}), "diff_nodes and the pages disagree on cut items"
    for k, i in diff_items.items():
        if i["change"] == "revised":
            on_page = {_norm(t) for t in (after[k]["item"].get("bullets") or [])}
            assert {_norm(t) for t in i["bullets_added"]} == on_page - {
                _norm(t) for t in (before[k]["item"].get("bullets") or [])}, f"{k}: bullets differ from diff_nodes"

    # Sections: Projects above Experience, say. Read from the two versions' section order.
    labels = {"education": "Education", "experience": "Experience", "projects": "Projects",
              "skills": "Skills", "achievements": "Achievements"}
    default_order = ["education", "experience", "projects", "skills", "achievements"]
    a_order = [x for x in (inp["after"].get("_section_order") or default_order)
               if x in default_order]
    b_order = [x for x in (inp["before"].get("_section_order") or default_order) if x in a_order]
    a_rank = {x: i for i, x in enumerate(x for x in a_order if x in b_order)}
    b_rank = {x: i for i, x in enumerate(b_order)}
    by_id = {r["id"]: r for r in requirements}
    section_of = {"experience": "experiences", "projects": "projects"}
    for sec in b_order:
        if a_rank[sec] < b_rank[sec]:
            jumped = [labels[x] for x in b_order if b_rank[x] < b_rank[sec] and a_rank[x] > a_rank[sec]]
            # What the section's items serve, the two heaviest requirements first.
            ids = {r["id"] for k, v in after.items() if v["section"] == section_of.get(sec)
                   for r in serves(k)}
            top = sorted(ids, key=lambda i: -by_id[i]["criticality"])[:2]
            moved.insert(0, {"scope": "section", "section": labels[sec], "above": jumped,
                             "serves": [{"id": i, "short": by_id[i]["short"]} for i in top]})

    names = lambda c: [s.get("name") for s in c.get("skills_ranked") or [] if s.get("name")]  # noqa: E731
    b_names, a_names = names(inp["before"]), names(inp["after"])
    gained = list(diff.get("skills_added") or [])
    lost = list(diff.get("skills_removed") or [])
    assert set(gained) == {n for n in a_names if n.lower() not in {x.lower() for x in b_names}}, "diff_nodes and the pages disagree"
    skills = {
        "before_count": len(b_names), "after_count": len(a_names), "first": a_names[:5],
        "added": [{"name": n, "serves": [{"id": r["id"], "short": r["short"]} for r in requirements
                                          if n.lower() in {t.lower() for t in r["terms"]}][:1]} for n in gained],
        "removed": lost,
        "led_by": [{"name": n, "serves": [{"id": r["id"], "short": r["short"]} for r in requirements
                                          if n.lower() in {t.lower() for t in r["terms"]}][:1]}
                   for n in a_names[:3]],
    }
    groups = []
    for gid, title, entries in (("added", "Added from your graph", added), ("moved", "Moved up", moved),
                                ("reworded", "Reworded", reworded), ("cut", "Cut", cut)):
        if entries:
            groups.append({"id": gid, "title": title, "entries": entries})
    if gained or lost:
        groups.append({"id": "skills", "title": "Skills trimmed", "entries": [skills]})
    return groups


def media(files: dict, groups_input: dict | None = None) -> dict:
    import pypdfium2 as pdfium

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for old in IMG_DIR.glob("resume-*.webp"):
        old.unlink()
    pdfs = {label: files[label]["pdf"] if isinstance(files[label], dict) else files[label]
            for label in ("before", "after")}
    items = {label: group_items(pdf_lines(pdf)) for label, pdf in pdfs.items()}

    # Both pages are whole pages, never cropped. How full each one is: the bottom edge of its
    # last line of text as a fraction of the page height (0 is the top). `tests/test_site_demo.py`
    # holds both to the bottom tenth of the page.
    fill = {label: round(max(ln["y"] + ln["h"] for it in its for ln in it["lines"]), 4)
            for label, its in items.items()}

    pages = {}
    for label, pdf in pdfs.items():
        doc = pdfium.PdfDocument(str(pdf))
        page = doc[0]
        w_pt, _ = page.get_size()
        sources = []
        for px in PAGE_W_PX:
            image = page.render(scale=px / w_pt).to_pil().convert("RGB")
            name = f"resume-{label}-{px}.webp"
            image.save(IMG_DIR / name, "WEBP", quality=80, method=6)
            sources.append({"src": f"/demo/{name}", "width": image.width, "height": image.height})
        doc.close()
        pages[label] = sources

    before, after = items["before"], items["after"]
    matcher = difflib.SequenceMatcher(None, [i["text"] for i in before], [i["text"] for i in after],
                                      autojunk=False)
    removed, added, changes = [], [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        removed += before[i1:i2]
        added += after[j1:j2]
        changes.append({"removed": [i["text"] for i in before[i1:i2]],
                        "added": [i["text"] for i in after[j1:j2]]})

    def marks(chosen):
        return [{k: rect(it["lines"])[k] for k in ("x", "y", "w", "h")} for it in chosen]

    out = {
        "label": LABEL,
        "fill": fill,
        "posting": {
            "title": S.POSTING_TITLE, "company": S.POSTING_COMPANY, "location": S.POSTING_LOCATION,
            "requirements": [{"id": q["id"], "short": q["short"], "text": q["text"], "type": q["type"]}
                             for q in S.REQUIREMENTS],
        },
        "before": {"sources": pages["before"], "marks": marks(removed)},
        "after": {"sources": pages["after"], "marks": marks(added)},
        "changes": changes,
        "counts": {"removed": len(removed), "added": len(added)},
    }
    if groups_input is not None:
        out["groups"] = build_groups(groups_input, S.REQUIREMENTS, S.CUT_REASONS)
    return out


# --- main -------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tectonic", help="path to the tectonic executable (else tectonic/pdflatex on PATH)")
    ap.add_argument("--keep", help="copy the scratch directory (PDFs, .tex, raw tool answers) here")
    ap.add_argument("--media-only", metavar="DIR", help="skip the capture; re-render before.pdf/after.pdf from DIR")
    args = ap.parse_args(argv)

    if args.media_only:
        folder = Path(args.media_only)
        files = {"before": folder / "before.pdf", "after": folder / "after.pdf"}
        BEFORE_AFTER.write_text(json.dumps(media(files), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", BEFORE_AFTER)
        return 0

    engine_dir, engine_name = find_engine(args.tectonic)
    scratch = Path(tempfile.mkdtemp(prefix="art_site_demo_"))
    data_dir = scratch / "art_data"
    data_dir.mkdir()
    home_art = (Path.home() / ".art").resolve()
    assert home_art not in data_dir.resolve().parents and data_dir.resolve() != home_art
    env = clean_env(data_dir, engine_dir)
    print(f"scratch ART_DATA_DIR: {data_dir}")

    art = Art(data_dir, env)
    events, kept = capture(art, scratch)

    # What the page shows must not carry this machine's paths or anything personal.
    blob = json.dumps(events, ensure_ascii=False)
    for needle in (str(scratch), str(scratch).replace("\\", "/"), str(Path.home()),
                   str(Path.home()).replace("\\", "/"), "AppData", os.environ.get("USERNAME", "\0")):
        assert needle not in blob, f"transcript leaks {needle!r}"

    for ev in events:
        ev["ms"] = duration_ms(ev)
    total = sum(ev["ms"] for ev in events)
    from harness import ART_VERSION
    from harness.contract import CONTRACT_VERSION

    engine = subprocess.run(
        [sys.executable, "-c",
         "from harness.render_cache import engine_id; print(engine_id())"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT).stdout.strip() or engine_name
    transcript = {
        "meta": {
            "label": LABEL,
            "generated_by": "scripts/build_site_demo.py",
            "art_version": ART_VERSION, "contract_version": CONTRACT_VERSION,
            "latex_engine": engine, "jev": "off (no TypeSafe key)",
            "profile": "scripts/site_demo/sample_resume.md and sample_repos.md (synthetic: no real person)",
            "posting": "scripts/site_demo/sample_posting.txt (written for this demo)",
            "authored": "Lines from the user and the agent, the records the agent extracted, the "
                        "requirements it read from the posting and its plan were written by hand. "
                        "Tool calls and what ART returned were captured.",
            "elision": "Long tool answers are shortened for display: lists are cut, strings "
                       "shortened and fields left out, each marked with an ellipsis.",
            "pacing": "Playback runs at a fixed rate; it is not the real duration of any call.",
            "total_ms": total,
        },
        "phases": PHASES,
        "events": events,
    }
    TRANSCRIPT.parent.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT.write_text(json.dumps(transcript, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {TRANSCRIPT.relative_to(ROOT)}: {len(events)} steps, {total / 1000:.0f} s of playback")

    (scratch / "groups_input.json").write_text(
        json.dumps(kept["groups_input"], indent=1, ensure_ascii=False), encoding="utf-8")
    ba = media(kept["files"], kept["groups_input"])
    for label, bottom in ba["fill"].items():
        assert bottom >= MIN_FILL, (f"the {label} page ends at {bottom:.0%} of the page height; tune "
                                    f"scripts/site_demo/sample_resume.md or the plan until both pages "
                                    f"fill past {MIN_FILL:.0%}")
    BEFORE_AFTER.write_text(json.dumps(ba, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {BEFORE_AFTER.relative_to(ROOT)}: {ba['counts']} changed items")
    for p in sorted(IMG_DIR.glob("resume-*.webp")):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KB")

    if args.keep:
        shutil.copytree(scratch, args.keep, dirs_exist_ok=True)
        (Path(args.keep) / "raw_tool_answers.json").write_text(
            json.dumps(art.raw, indent=1, ensure_ascii=False), encoding="utf-8")
        print("kept scratch copy in", args.keep)
    shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
