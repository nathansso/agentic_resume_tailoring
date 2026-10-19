"""The `render` tool (issue #201): a tailoring node as `.tex` and PDF on disk.

The host calls it once a plan has committed. It renders exactly what the node
holds — no silent one-page trimming, which is `execute_plan`'s finalize and the
host's call to make — the way the web app's download does: the node's manual
`.tex` when the user edited one in the editor, else the formatter's layout of
its content. Files land in `$ART_DATA_DIR/applications/<Company>_<Role>/`.

The reply carries the page count and the #200 line budget, so a host that sees
two pages knows which bullets to cut without compiling again. With no LaTeX
engine it still writes the `.tex` and says how to get a PDF.

A copy goes where the user asked (#394): `save_to` (a folder path, or `downloads`,
`desktop`, `documents`; `harness/destinations.py`), else the profile's
`default_output_dir`. With neither, the reply says `destination: "ask_user"`, so
the host asks once and passes `remember=true` with the answer.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from sqlmodel import Session, select

import database.db as _db
from database.models import Education, JobDescription
from harness import tree
from harness.tools import edu_key

log = logging.getLogger(__name__)

ENGINE_HINT = ("No LaTeX engine found, so only the .tex was written. Install tectonic "
               "(https://tectonic-typesetting.github.io) or pdflatex and render again.")


def applications_dir() -> Path:
    from config import APP_DATA_DIR
    return Path(APP_DATA_DIR) / "applications"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_") or "Untitled"


def _education_rows(user_id: UUID, content: Dict):
    """The user's education rows, with the node's own education fields laid
    over them. The formatter reads education from the store, so without this a
    job-scoped rule applied to the node (e.g. a graduation date, #192) would
    never reach the page. Copies only; nothing is written back."""
    with Session(_db.engine) as session:
        rows = list(session.exec(
            select(Education).where(Education.user_id == user_id)
            .order_by(Education.seq.is_(None), Education.seq, Education.created_at,
                      Education.education_id)).all())
        rows = [Education(**r.model_dump()) for r in rows]   # detached copies
    by_key = {edu_key(e): e for e in content.get("education") or []}
    for row in rows:
        over = by_key.get(edu_key({"institution": row.institution, "degree": row.degree}))
        if over:
            for field in ("degree", "start_date", "end_date"):
                if over.get(field):
                    setattr(row, field, over[field])
    return rows


def generated_tex(user_id: UUID, content: Dict) -> str:
    """What the formatter makes of `content`, with its education fields laid over the store's."""
    from agents.formatter import ResumeFormatterAgent

    agent = ResumeFormatterAgent(user_id)
    rows = _education_rows(user_id, content)
    agent._get_education = lambda: rows          # this render only
    return agent.format_tex(content, section_order=content.get("_section_order"))


def render(user_id: UUID, job_id: str, node_id: Optional[str] = None,
           format: str = "pdf", save_to: Optional[str] = None, remember: bool = False,
           overwrite: bool = False, save_tex: bool = False) -> Dict[str, Any]:
    from agents.formatter import _compile_tex_to_pdf, _pdf_page_count
    from harness.toolchain import latex_engine

    try:
        jid = UUID(str(job_id))
    except ValueError:
        return {"error": {"code": "not_found", "message": f"No job {job_id!r}."}}
    with Session(_db.engine) as session:
        job = session.get(JobDescription, jid)
        if job is None or job.user_id != user_id:
            return {"error": {"code": "not_found", "message": f"No job {job_id!r}."}}
        company, title = job.company, job.title

    if node_id:
        nodes = {n["node_id"]: n for n in tree.history(user_id, jid)}
        if node_id not in nodes:
            return {"error": {"code": "not_found", "message": f"No node {node_id!r} on this job."}}
        node = tree.get_node(user_id, node_id)
    else:
        node = tree.get_head(user_id, jid)["head"]
        if node is None:
            return {"error": {"code": "no_version", "message": (
                "This job has no committed version yet; run execute_plan first.")}}

    # Where the user's copy goes (#394), settled before anything is written.
    dest = _destination(user_id, save_to, remember)
    if "error" in dest:
        return dest

    def done(res: Dict[str, Any]) -> Dict[str, Any]:
        return _deliver(user_id, res, dest, company, title,
                        want_tex=format == "tex" or save_tex, overwrite=overwrite)

    content = node.get("content") or {}
    edited = node.get("edited_tex")
    if edited:
        tex, source = edited, "edited"
    else:
        tex, source = generated_tex(user_id, content), "generated"

    out_dir = applications_dir() / f"{_slug(company)}_{_slug(title)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / "resume.tex"
    tex_path.write_text(tex, encoding="utf-8")
    result: Dict[str, Any] = {
        "job_id": str(jid), "node_id": node["node_id"], "source": source,
        "tex_path": str(tex_path), "pdf_path": None, "pages": None, "line_budget": {},
        "hint": None,
    }

    # Find (or, on first need, download: #384) the engine before measuring, so the
    # first render on a machine without LaTeX measures and compiles like any other.
    engine, no_engine = None, None
    if format != "tex" or not edited:
        engine, no_engine = latex_engine()

    if not edited:
        try:
            from harness.render_cache import page_budget
            result["line_budget"] = page_budget(content)
        except Exception as exc:          # no engine to measure with, or a read-only store
            log.info("line budget unavailable: %s", exc)

    if format == "tex":
        return done(result)
    if engine is None:
        result["hint"] = f"{ENGINE_HINT} Automatic setup: {no_engine}." if no_engine else ENGINE_HINT
        return done(result)
    try:
        pdf = _compile_tex_to_pdf(tex)
    except RuntimeError as exc:
        result["hint"] = f"The .tex did not compile: {str(exc)[:500]}"
        return done(result)
    pdf_path = out_dir / "resume.pdf"
    pdf_path.write_bytes(pdf)
    result.update(pdf_path=str(pdf_path), pages=_pdf_page_count(pdf))
    if result["pages"] and result["pages"] > 1:
        result["hint"] = ("The page runs over one page. Cut with patch_plan using "
                          "line_budget.cut_hints, then render again.")
    return done(result)


# ── the user's copy (#394) ───────────────────────────────────────────────────

ASK_USER_NOTE = (
    "No save location is set for this user, so the files are only under the ART data "
    "directory. Ask the user where to save their resumes: Downloads, Desktop, Documents or "
    "a folder path. Then call render again with save_to and remember=true, so you never ask "
    "again. If they have no preference, use save_to=\"downloads\".")


def _destination(user_id: UUID, save_to: Optional[str], remember: bool) -> Dict[str, Any]:
    """Where the copy goes: `save_to`, else the stored default, else ask the user."""
    from database.models import User
    from harness import destinations

    if remember and not (save_to or "").strip():
        return {"error": {"code": "invalid_arguments", "message": (
            "remember=true needs save_to: the folder to remember.")}}
    with Session(_db.engine) as session:
        user = session.get(User, user_id)
        name = user.name if user else None
        stored = (user.default_output_dir or "").strip() if user else ""
    if (save_to or "").strip():
        try:
            value = destinations.parse(save_to)
        except destinations.DestinationError as exc:
            return {"error": {"code": "invalid_arguments", "message": str(exc),
                              "suggestions": list(destinations.WELL_KNOWN)}}
        return {"origin": "save_to", "value": value, "remember": remember, "name": name}
    if stored:
        return {"origin": "default", "value": stored, "remember": False, "name": name}
    return {"origin": "ask_user", "name": name}


def _deliver(user_id: UUID, result: Dict[str, Any], dest: Dict[str, Any], company: str,
             title: str, want_tex: bool, overwrite: bool) -> Dict[str, Any]:
    """Copy the PDF (and the `.tex` when asked) to the destination, and say what happened.
    A failed copy never fails the render: the files under the data dir stand."""
    from database.models import User
    from harness import destinations

    result.update(destination=dest["origin"], save_dir=None, saved_paths=[],
                  remembered=False, destination_note=None)
    if dest["origin"] == "ask_user":
        result["destination_note"] = ASK_USER_NOTE
        return result
    if dest.get("remember"):
        with Session(_db.engine) as session:
            user = session.get(User, user_id)
            if user is not None:
                user.default_output_dir = dest["value"]
                session.add(user)
                session.commit()
                result["remembered"] = True
    try:
        directory = destinations.resolve(dest["value"])
    except destinations.DestinationError as exc:      # a stored default edited by hand
        result["destination_note"] = (
            f"The saved location is not usable: {exc} Ask the user where to save, then "
            "render again with save_to and remember=true.")
        return result
    result["save_dir"] = str(directory)
    sources = {}
    if result.get("pdf_path"):
        sources[".pdf"] = Path(result["pdf_path"])
    if want_tex and result.get("tex_path"):
        sources[".tex"] = Path(result["tex_path"])
    if not sources:
        result["destination_note"] = (
            "Nothing was copied: no PDF was made (see hint). Pass save_tex=true to copy "
            "the .tex instead.")
        return result
    stem = destinations.output_stem(dest.get("name"), company, title)
    try:
        written = destinations.copy_files(sources, directory, stem, overwrite=overwrite)
    except OSError as exc:
        result["destination_note"] = f"Could not save to {directory}: {exc}"
        return result
    result["saved_paths"] = [str(p) for p in written]
    return result
