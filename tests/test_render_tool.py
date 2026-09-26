"""The `render` and `update_profile` tools (issue #201).

`render` writes a job's version as .tex (and PDF when a LaTeX engine exists)
under the data directory: the user's own .tex edits win, a job-scoped rule's
education values reach the page, and nothing is trimmed behind the host's back.
"""

import pytest
from sqlmodel import Session, select

from database.models import Education, UserJobResult
from harness.contract import invoke

CONTENT = {"experiences": [{"title": "Data Science Intern", "company": "IDX Exchange",
                            "start_date": "Jun 2024", "end_date": "Sep 2024",
                            "bullets": ["Built a gradient-boosted ensemble on 200K listings"]}],
           "projects": [], "skills_ranked": [{"name": "Python", "category": "Language"}]}


@pytest.fixture()
def job(kg, isolated_engine, monkeypatch):
    """The `kg` user's job, with one committed host version."""
    import agents.formatter as fmt
    from harness import tree

    # The formatter binds `engine` at import (#175), so the header query would
    # read whichever store the first test to import it used.
    monkeypatch.setattr(fmt, "engine", isolated_engine)

    with Session(isolated_engine) as s:
        job_id = s.exec(select(UserJobResult).where(UserJobResult.user_id == kg)).first().job_id
    tree.commit_node(kg, job_id, content=CONTENT, source="host")
    return kg, str(job_id)


def _no_engine(monkeypatch):
    # render_cache binds the lookup at import, so patch both (and import it
    # first, or it would bind the patched lambda for the rest of the session).
    import agents.formatter as fmt
    import harness.render_cache as rc
    monkeypatch.setattr(fmt, "_find_latex_engine", lambda: None)
    monkeypatch.setattr(rc, "_find_latex_engine", lambda: None)


def test_render_writes_the_tex_under_the_data_dir_and_says_how_to_get_a_pdf(job, monkeypatch,
                                                                            tmp_path):
    uid, job_id = job
    _no_engine(monkeypatch)
    out = invoke("render", uid, {"job_id": job_id})
    assert out["error"] is None and out["source"] == "generated"
    assert out["tex_path"].startswith(str(tmp_path)) and "Rippling_Data_Scientist" in out["tex_path"]
    tex = open(out["tex_path"], encoding="utf-8").read()
    assert "Test User" in tex and "gradient-boosted ensemble" in tex
    assert out["pdf_path"] is None and "tectonic" in out["hint"]


def test_the_users_own_tex_edits_win(job, monkeypatch):
    from harness import tree

    uid, job_id = job
    _no_engine(monkeypatch)
    tree.commit_node(uid, job_id, content=CONTENT, source="editor",
                     edited_tex="% my own edit\n\\documentclass{article}")
    out = invoke("render", uid, {"job_id": job_id, "format": "tex"})
    assert out["source"] == "edited"
    assert open(out["tex_path"], encoding="utf-8").read().startswith("% my own edit")


def test_a_job_rule_on_education_reaches_the_page(job, monkeypatch, isolated_engine):
    from harness import tree

    uid, job_id = job
    _no_engine(monkeypatch)
    with Session(isolated_engine) as s:
        edu = s.exec(select(Education).where(Education.user_id == uid)).one()
        edu.end_date = "June 2027"
        s.add(edu)
        s.commit()
    content = dict(CONTENT, education=[{"institution": "UC San Diego",
                                        "degree": "M.S. Data Science", "end_date": "Dec 2027"}])
    tree.commit_node(uid, job_id, content=content, source="host")
    tex = open(invoke("render", uid, {"job_id": job_id})["tex_path"], encoding="utf-8").read()
    assert "Dec 2027" in tex and "June 2027" not in tex
    with Session(isolated_engine) as s:          # the stored row is untouched
        assert s.exec(select(Education).where(Education.user_id == uid)).one().end_date == "June 2027"


def test_render_reports_unknown_jobs_nodes_and_jobs_without_a_version(job, kg):
    uid, job_id = job
    assert invoke("render", uid, {"job_id": "nope"})["error"]["code"] == "not_found"
    assert invoke("render", uid, {"job_id": job_id, "node_id": "x"})["error"]["code"] == "not_found"
    fresh = invoke("open_job", uid, {"jd_text": "Analyst role", "metadata": {
        "title": "Analyst", "company": "Beta"}})["job_id"]
    assert invoke("render", uid, {"job_id": fresh})["error"]["code"] == "no_version"


@pytest.mark.integration
def test_render_compiles_a_one_page_pdf(job):
    from agents.formatter import _find_latex_engine

    if _find_latex_engine() is None:
        pytest.skip("no LaTeX engine (tectonic/pdflatex) installed")
    uid, job_id = job
    out = invoke("render", uid, {"job_id": job_id})
    assert out["pages"] == 1 and open(out["pdf_path"], "rb").read(4) == b"%PDF"
    assert out["line_budget"]["over_by"] <= 0


# ── update_profile ───────────────────────────────────────────────────────────

def test_update_profile_sets_header_fields_only(kg):
    out = invoke("update_profile", kg, {"fields": {"name": "Ada Lovelace",
                                                    "location": "London", "phone": ""}})
    assert out["name"] == "Ada Lovelace" and out["location"] == "London" and out["phone"] is None
    assert invoke("get_profile", kg, {})["name"] == "Ada Lovelace"
    bad = invoke("update_profile", kg, {"fields": {"password_hash": "x"}})
    assert bad["error"]["code"] == "invalid_arguments"
    assert invoke("update_profile", kg, {"fields": {"name": " "}})["error"]["code"] == \
        "invalid_arguments"
    assert invoke("get_profile", kg, {})["name"] == "Ada Lovelace"


def test_both_tools_are_writes():
    from harness.contract import BY_NAME
    assert not BY_NAME["render"].read_only and not BY_NAME["update_profile"].read_only
