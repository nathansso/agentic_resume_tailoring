"""The `render` tool (issue #201): a tailoring node as `.tex` and PDF on disk.

The host calls it once a plan has committed. It renders exactly what the node
holds — no silent one-page trimming, which is `execute_plan`'s finalize and the
host's call to make — the way the web app's download does: the node's manual
`.tex` when the user edited one in the editor, else the formatter's layout of
its content. Files land in `$ART_DATA_DIR/applications/<Company>_<Role>/`.

The reply carries the page count and the #200 line budget, so a host that sees
two pages knows which bullets to cut without compiling again. With no LaTeX
engine it still writes the `.tex` and says how to get a PDF.

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


def render(user_id: UUID, job_id: str, node_id: Optional[str] = None,
           format: str = "pdf") -> Dict[str, Any]:
    from agents.formatter import (
        ResumeFormatterAgent, _compile_tex_to_pdf, _find_latex_engine, _pdf_page_count,
    )

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

    content = node.get("content") or {}
    edited = node.get("edited_tex")
    if edited:
        tex, source = edited, "edited"
    else:
        agent = ResumeFormatterAgent(user_id)
        rows = _education_rows(user_id, content)
        agent._get_education = lambda: rows          # this render only
        tex, source = agent.format_tex(content, section_order=content.get("_section_order")), \
            "generated"

    out_dir = applications_dir() / f"{_slug(company)}_{_slug(title)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / "resume.tex"
    tex_path.write_text(tex, encoding="utf-8")
    result: Dict[str, Any] = {
        "job_id": str(jid), "node_id": node["node_id"], "source": source,
        "tex_path": str(tex_path), "pdf_path": None, "pages": None, "line_budget": {},
        "hint": None,
    }

    if not edited:
        try:
            from harness.render_cache import page_budget
            result["line_budget"] = page_budget(content)
        except Exception as exc:          # no engine to measure with, or a read-only store
            log.info("line budget unavailable: %s", exc)

    if format == "tex":
        return result
    if _find_latex_engine() is None:
        result["hint"] = ENGINE_HINT
        return result
    try:
        pdf = _compile_tex_to_pdf(tex)
    except RuntimeError as exc:
        result["hint"] = f"The .tex did not compile: {str(exc)[:500]}"
        return result
    pdf_path = out_dir / "resume.pdf"
    pdf_path.write_bytes(pdf)
    result.update(pdf_path=str(pdf_path), pages=_pdf_page_count(pdf))
    if result["pages"] and result["pages"] > 1:
        result["hint"] = ("The page runs over one page. Cut with patch_plan using "
                          "line_budget.cut_hints, then render again.")
    return result
