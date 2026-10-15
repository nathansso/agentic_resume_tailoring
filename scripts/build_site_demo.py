#!/usr/bin/env python
"""Rebuild the landing site's demo from a real ART run (issue #275).

    python scripts/build_site_demo.py                  # capture + media
    python scripts/build_site_demo.py --tectonic C:\\tools\\tectonic.exe
    python scripts/build_site_demo.py --media-only     # re-render images from the saved PDFs

What it does, in order:

1. Creates a scratch `ART_DATA_DIR` (a new temp directory, never `~/.art`), strips every
   database, key and `ART_*` variable from the environment, and turns Jev off.
2. Drives the real `art` tools through `python -m harness.entry <tool> --args <json>`, the
   same entry point the `art` script runs, in the order `/art:setup` and `/art:tailor` take.
   The "user" and "agent" lines and the agent's records and plan are in
   `scripts/site_demo/demo_script.py`; everything ART answers is captured as it comes back.
3. Writes `site/src/components/demo/transcript.json`, the replay's only data.
4. Renders the untailored and the tailored version to PDF (tectonic or pdflatex), rasterises
   page 1 of each to WebP under `site/public/demo/`, finds the lines that differ by diffing
   the PDFs' own text, and writes `site/src/components/demo/before-after.json`.

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


def slim_plan_result(result: dict, keys: list[str]) -> dict:
    """An execute_plan answer: the verdict fields, and per node only the ones that say what
    happened. Every cut is marked (`"…": "N more fields"`), at the top and on each node."""
    out = pick(result, keys)
    if "nodes" in out:
        out["nodes"] = [pick(n, NODE_KEYS) for n in result["nodes"]]
    return out


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

    # -- set up
    say("setup", "user", S.USER_SETUP)
    say("setup", "agent", S.AGENT_SETUP_1)
    records = list(S.RECORDS) + [{"kind": "skill", "data": {"name": n, "category": c, "proficiency": 3}}
                                 for n, c in S.SKILLS]
    r = tool("setup", "upsert_items", {"records": records, "source": "resume.md"},
             show_result=lambda r: pick(r, ["counts", "results"]), items=3, chars=110)
    created = r["counts"].get("created", 0)
    assert created == len(records), r["counts"]
    say("setup", "agent", S.AGENT_SETUP_2.format(created=created))
    tool("setup", "update_profile", {"fields": S.PROFILE})
    listed = tool("setup", "list_items", {}, items=3, chars=110)
    kinds = [i["kind"] for i in listed["items"]]
    say("setup", "agent", S.AGENT_SETUP_3.format(
        n_exp=kinds.count("experience"), n_proj=kinds.count("project"), n_skill=kinds.count("skill")))

    # -- posting
    posting = POSTING_TXT.read_text(encoding="utf-8")
    say("posting", "user", S.USER_TAILOR_TEMPLATE.format(
        title=S.POSTING_TITLE, company=S.POSTING_COMPANY, lines=len(posting.splitlines())))
    say("posting", "agent", S.AGENT_POSTING_1)
    tool("posting", "art_briefing", {"role_family": S.ROLE_FAMILY},
         show_result=lambda r: pick(r, ["pins", "preferences", "counts", "role_family"]))
    say("posting", "agent", S.AGENT_POSTING_2.format(n_req=len(S.REQUIREMENTS)))
    opened = tool("posting", "open_job",
                  {"jd_text": posting, "requirements": S.REQUIREMENTS,
                   "metadata": {"title": S.POSTING_TITLE, "company": S.POSTING_COMPANY,
                                "role_family": S.ROLE_FAMILY}},
                  show_result=lambda r: pick(r, ["job_id", "title", "company", "requirements",
                                                 "role_family", "skill_matches", "unmatched_terms"]),
                  items=3, chars=110)
    job_id = opened["job_id"]
    say("posting", "agent", S.AGENT_POSTING_3.format(n_match=len(opened["skill_matches"])))
    say("posting", "agent", S.AGENT_BASE)
    base = tool("posting", "execute_plan",
                {"program": {"job_id": job_id, "parent": None, "nodes": []}},
                show_result=lambda r: {"committed": r["committed"], "node_id": r["node_id"],
                                       "line_budget": r["line_budget"], "violations": r["violations"],
                                       "metrics": {"final": {"gates": r["metrics"]["final"]["gates"]},
                                                   "…": "guards, targets and the ATS-style report left out"}})
    assert base["committed"], base
    base_node = base["node_id"]
    say("posting", "agent", S.AGENT_READ)
    exp = tool("posting", "get_item", {"key": S.TAMARACK}, chars=120)
    prj = art("get_item", {"key": S.TRANSIT})          # read too; the replay shows the first only
    assert not prj.get("error")

    # -- plan
    tool("plan", "suggest_actions", {"job_id": job_id}, items=2, chars=100)
    events.append({"phase": "plan", "type": "agent", "text": S.AGENT_PLAN_INTRO,
                   "items": S.AGENT_PLAN_ITEMS, "outro": S.AGENT_PLAN_OUTRO, "note": S.SLIP_NOTE})

    # -- approval
    say("approve", "user", S.USER_APPROVE)

    # -- gate check
    def evidence(item_key: str, record_bullets: list[str], start_of_source: str) -> str:
        for i, text in enumerate(record_bullets):
            if text.startswith(start_of_source):
                return f"{item_key}#b{i}"
        raise SystemExit(f"{item_key}: no source bullet starts with {start_of_source!r}")

    exp_bullets = exp["record"]["bullets"]
    prj_bullets = [b["content"] for b in prj["record"]["blurbs"]]

    def bullets(item_key, source_bullets, pairs):
        return [{"text": text, "cites": [evidence(item_key, source_bullets, start)]}
                for text, start in pairs]

    program = {
        "job_id": job_id, "parent": base_node,
        "nodes": [
            {"id": "n1", "op": "revise", "item_key": S.TAMARACK, "strategy": "quantify",
             "keywords": ["predict", "SQL", "delays"],
             "bullets": bullets(S.TAMARACK, exp_bullets, S.TAMARACK_BULLETS)},
            {"id": "n2", "op": "keep", "item_key": S.TA},
            {"id": "n3", "op": "revise", "item_key": S.TRANSIT, "strategy": "keyword_weave",
             "keywords": ["dashboards", "delays"],
             "bullets": bullets(S.TRANSIT, prj_bullets, S.TRANSIT_BULLETS)},
            {"id": "n4", "op": "keep", "item_key": S.DINING},
            {"id": "n5", "op": "keep", "item_key": S.RECYCLING},
            {"id": "n6", "op": "delete", "item_key": S.BUDGET, "because": S.DROP_REASON},
        ],
        "skills": S.SKILL_ORDER,
        "host": {"name": "claude-code"},
    }
    say("gates", "agent", S.AGENT_GATES_1)
    first = art("execute_plan", {"program": program})
    assert not first.get("error"), first
    node_status = {n["id"]: n for n in first["nodes"]}
    assert node_status["n1"]["status"] == "reverted", node_status["n1"]
    assert "consistency" in json.dumps(node_status["n1"]), node_status["n1"]
    assert node_status["n3"]["status"] == "reverted", node_status["n3"]
    assert "no_target_improved" in json.dumps(node_status["n3"]), node_status["n3"]
    assert node_status["n6"]["status"] == "accepted", node_status["n6"]
    events.append({
        "phase": "gates", "type": "tool", "name": "execute_plan",
        "args": sanitize(shrink({"program": program}, items=6, chars=118), art.data_dir),
        "result": sanitize(shrink(pick(first, ["committed", "node_id", "nodes", "violations"]),
                                  items=7, chars=230), art.data_dir)})
    say("gates", "agent", S.AGENT_GATES_2)
    assert first["committed"], "the other nodes should still commit"
    v2 = first["node_id"]
    fixed = json.loads(json.dumps(program["nodes"][0]))
    fixed["bullets"][1]["text"] = S.ORIGINAL_REPORT + "."
    assert S.SLIP_SUFFIX not in fixed["bullets"][1]["text"]
    second = tool("gates", "execute_plan",
                  {"program": {"job_id": job_id, "parent": v2, "nodes": [fixed]}},
                  show_result=lambda r: slim_plan_result(r, ["committed", "node_id", "parent", "nodes", "violations"]),
                  items=7, chars=230)
    assert second["committed"], second
    assert second["nodes"][0]["status"] == "accepted", second["nodes"][0]
    after_node = second["node_id"]
    say("gates", "agent", S.AGENT_GATES_3)

    # -- render
    out = {}
    for label, node in (("before", base_node), ("after", after_node)):
        rendered = tool("render", "render", {"job_id": job_id, "node_id": node},
                        show_result=lambda r: pick(r, ["node_id", "pdf_path", "pages", "source"]))
        assert rendered["pdf_path"], rendered.get("hint")
        pdf = tex_dir / f"{label}.pdf"
        shutil.copyfile(rendered["pdf_path"], pdf)
        (tex_dir / f"{label}.tex").write_text(Path(rendered["tex_path"]).read_text(encoding="utf-8"),
                                              encoding="utf-8")
        out[label] = {"pdf": pdf, "pages": rendered["pages"], "shown_path": rendered["pdf_path"]}
    assert out["after"]["pages"] == 1, f"tailored resume is {out['after']['pages']} pages"
    say("render", "agent", S.AGENT_DONE.format(pdf=sanitize(out["after"]["shown_path"], art.data_dir)))
    keep.update(job_id=job_id, base_node=base_node, after_node=after_node, files=out)
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


def media(files: dict) -> dict:
    import pypdfium2 as pdfium

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for old in IMG_DIR.glob("resume-*.webp"):
        old.unlink()
    pdfs = {label: files[label]["pdf"] if isinstance(files[label], dict) else files[label]
            for label in ("before", "after")}
    items = {label: group_items(pdf_lines(pdf)) for label, pdf in pdfs.items()}
    # Both pages are cropped at the same height, just below the lowest line of either, so the
    # blank foot of a one-page resume does not take up the screen. Boxes are fractions of
    # the cropped image.
    lowest = max(ln["y"] + ln["h"] for its in items.values() for it in its for ln in it["lines"])
    keep = min(1.0, lowest + 0.045)

    pages = {}
    for label, pdf in pdfs.items():
        doc = pdfium.PdfDocument(str(pdf))
        page = doc[0]
        w_pt, _ = page.get_size()
        sources = []
        for px in PAGE_W_PX:
            image = page.render(scale=px / w_pt).to_pil().convert("RGB")
            image = image.crop((0, 0, image.width, round(image.height * keep)))
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
        out = []
        for it in chosen:
            r = rect(it["lines"])
            out.append({"x": r["x"], "y": round(r["y"] / keep, 4), "w": r["w"], "h": round(r["h"] / keep, 4)})
        return out

    return {
        "label": LABEL,
        "cropped_to": round(keep, 4),
        "before": {"sources": pages["before"], "marks": marks(removed)},
        "after": {"sources": pages["after"], "marks": marks(added)},
        "changes": changes,
        "counts": {"removed": len(removed), "added": len(added)},
    }


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
            "profile": "scripts/site_demo/sample_resume.md (synthetic: no real person)",
            "posting": "scripts/site_demo/sample_posting.txt (written for this demo)",
            "authored": "Lines from the user and the agent, the records the agent extracted and "
                        "its plan were written by hand. Tool calls and what ART returned were captured.",
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

    ba = media(kept["files"])
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
