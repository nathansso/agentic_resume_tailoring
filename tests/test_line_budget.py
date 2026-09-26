"""Line budget and block render cache (issue #200).

Unit tests (no LaTeX engine, run in CI): the per-bullet two-line gate, the
cache serving hits without compiling, the template hash, and the page budget
with its cut hints in `_trim_one_bullet`'s order.

Integration tests (need tectonic or pdflatex; skipped otherwise): measured
lines agree with an independent count taken from the formatter's real PDF,
and the page budget predicts the real page count for the formatter fixtures.
"""
import copy
import io
from pathlib import Path

import pytest
from sqlmodel import Session, select

import agents.formatter as fmt_module
from agents.checks import (
    LINE_COSTS,
    PAGE_LINE_BUDGET,
    achievement_text,
    bullet_id,
    bullet_line_violations,
    bullet_texts,
    exp_key,
    page_line_budget,
    proj_key,
)
from agents.formatter import (
    ResumeFormatterAgent,
    _JAKE_PREAMBLE,
    _compile_tex_to_pdf,
    _convert_inline,
    _pdf_page_count,
    _trim_one_bullet,
)
from database.models import BlockLineCache
from harness import render_cache
from harness.render_cache import bullet_lines, content_bullet_lines, measure_lines, template_hash
from test_latex_formatter import (
    _JAKE_CONTENT,
    _OVERFLOW_CONTENT,
    _no_latex_engine,
    _seed_jake_user,
)

ROOT = Path(__file__).resolve().parent.parent
TPL = template_hash(engine="test-engine")


class CountingMeasurer:
    """Fake measurer: one line per 100 characters, and a record of every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [len(t) // 100 + 1 for t in texts]


def _lines_for(content, n=1):
    return {bid: n for bid in bullet_texts(content)}


# ── per-bullet gate ───────────────────────────────────────────────────────────

def test_three_line_bullet_is_rejected():
    lines = {"exp:a|b#b0": 1, "exp:a|b#b1": 2, "exp:a|b#b2": 3}
    assert bullet_line_violations(lines) == [
        {"bullet": "exp:a|b#b2", "lines": 3, "max": 2}]
    assert bullet_line_violations(lines, max_lines=3) == []


# ── the cache ─────────────────────────────────────────────────────────────────

def test_cache_hits_never_call_the_measurer(isolated_engine):
    m = CountingMeasurer()
    a, b, c = "Built a thing " * 3, "Shipped another thing " * 10, "Third"

    assert bullet_lines([a, b, a], measurer=m, template=TPL) == [1, 3, 1]
    assert m.calls == [[a, b]]  # misses batched once, duplicates measured once

    assert bullet_lines([b, a], measurer=m, template=TPL) == [3, 1]
    assert len(m.calls) == 1  # all hits: no compile

    assert bullet_lines([a, c], measurer=m, template=TPL) == [1, 1]
    assert m.calls[1] == [c]  # only the miss

    with Session(isolated_engine) as s:
        assert len(s.exec(select(BlockLineCache)).all()) == 3


def test_a_new_template_is_a_cache_miss(isolated_engine):
    m = CountingMeasurer()
    bullet_lines(["Same text"], measurer=m, template=TPL)
    bullet_lines(["Same text"], measurer=m, template=template_hash(engine="other"))
    assert len(m.calls) == 2


def test_blank_bullets_are_zero_and_never_measured(isolated_engine):
    m = CountingMeasurer()
    assert bullet_lines(["", "   ", "Real"], measurer=m, template=TPL) == [0, 0, 1]
    assert m.calls == [["Real"]]


def test_template_hash_tracks_preamble_and_engine():
    base = template_hash(preamble=_JAKE_PREAMBLE, engine="e 1")
    assert base == template_hash(preamble=_JAKE_PREAMBLE, engine="e 1")
    assert base != template_hash(preamble=_JAKE_PREAMBLE + "%", engine="e 1")
    assert base != template_hash(preamble=_JAKE_PREAMBLE, engine="e 2")


def test_measurement_document_uses_the_formatters_bullet_tex():
    text = "Cut p95 latency 40% for **R&D** via [demo](https://x.io/a_b)"
    tex = render_cache.measurement_tex([text])
    assert tex.startswith(_JAKE_PREAMBLE)
    assert r"\resumeItem{" + _convert_inline(text) + "}" in tex
    assert r"\resumeItemListStart" in tex and r"\resumeSubHeadingListStart" in tex


# ── bullet ids ────────────────────────────────────────────────────────────────

def test_bullet_ids_are_stable_item_keys_plus_index():
    content = {
        "experiences": [{"title": "SWE", "company": "Acme", "bullets": ["a", " ", "c"]}],
        "projects": [{"name": "Tool", "bullets": ["p"]}],
        "achievements": [{"title": "Dean's List", "issuer": "UCSD", "date": "2024"}],
    }
    ids = bullet_texts(content)
    # A blank bullet never renders but keeps later bullets' indices stable.
    assert list(ids) == ["exp:swe|acme#b0", "exp:swe|acme#b2", "proj:tool#b0",
                         "ach:dean's list"]
    assert bullet_id(exp_key(content["experiences"][0]), 2) == "exp:swe|acme#b2"


def test_achievement_key_matches_the_harness():
    from harness.tools import ach_key
    a = {"title": "  Dean's List "}
    assert ach_key(a) in bullet_texts({"achievements": [a]})


def test_achievement_text_converts_to_the_formatters_latex():
    agent = ResumeFormatterAgent.__new__(ResumeFormatterAgent)
    agent._style = None
    a = {"title": "Winner, HackMIT", "issuer": "MIT", "date": "2024",
         "description": "Top 3 of 200 teams"}
    tex = agent._build_tex_achievements([a])
    assert r"\resumeItem{" + _convert_inline(achievement_text(a)) + "}" in tex


# ── page budget ───────────────────────────────────────────────────────────────

def test_under_budget_draft_has_no_hints():
    out = page_line_budget(_JAKE_CONTENT, _lines_for(_JAKE_CONTENT), skill_lines=2)
    assert out["budget"] == PAGE_LINE_BUDGET
    assert out["over_by"] <= 0
    assert out["cut_hints"] == []
    assert out["lines_used"] == round(out["budget"] + out["over_by"], 2)


def test_lines_used_counts_bullet_lines_plus_overhead():
    content = {"experiences": [{"title": "T", "company": "C", "bullets": ["x", "y"]}]}
    lines = {"exp:t|c#b0": 1, "exp:t|c#b1": 2}
    out = page_line_budget(content, lines, header=False)
    c = LINE_COSTS
    assert out["lines_used"] == round(
        c["section:experience"] + c["entry:experience"] + c["item_list"] + 3, 2)


def test_over_budget_hints_cover_the_overflow():
    lines = _lines_for(_OVERFLOW_CONTENT, n=2)
    out = page_line_budget(_OVERFLOW_CONTENT, lines, skill_lines=3)
    assert out["over_by"] > 0
    hints = out["cut_hints"]
    freed = sum(h["frees"] for h in hints)
    assert freed >= out["over_by"]
    # Minimal: without the last hint the overflow would not be covered.
    assert freed - hints[-1]["frees"] < out["over_by"]

    # Applying the hints really brings the draft under budget.
    content = copy.deepcopy(_OVERFLOW_CONTENT)
    cut = {h["bullet"] for h in hints if h["action"] == "cut_bullet"}
    dropped = {h["item"] for h in hints if h["action"] == "drop_item"}
    content["projects"] = [p for p in content["projects"] if proj_key(p) not in dropped]
    for key_fn, items in ((exp_key, content["experiences"]), (proj_key, content["projects"])):
        for item in items:
            item["bullets"] = [b for i, b in enumerate(item["bullets"])
                               if bullet_id(key_fn(item), i) not in cut]
    after = page_line_budget(content, _lines_for(content, n=2), skill_lines=3)
    assert after["over_by"] <= 0
    assert after["lines_used"] == pytest.approx(out["lines_used"] - freed, abs=0.01)


def _ladder(content):
    """What `_trim_one_bullet` removes, step by step, as hint-shaped tuples."""
    steps = []
    while True:
        nxt = _trim_one_bullet(content)
        if nxt is None:
            return steps
        before_p = {proj_key(p): p for p in content["projects"]}
        after_p = {proj_key(p) for p in nxt["projects"]}
        gone = [k for k in before_p if k not in after_p]
        if gone:
            steps.append(("drop_item", gone[0]))
        else:
            for key_fn, old, new in ((proj_key, content["projects"], nxt["projects"]),
                                     (exp_key, content["experiences"], nxt["experiences"])):
                for o, n in zip(old, new):
                    if len(n["bullets"]) < len(o["bullets"]):
                        steps.append(("cut_bullet", bullet_id(key_fn(o), len(o["bullets"]) - 1)))
        content = nxt


def test_hint_order_follows_the_formatters_trim_ladder():
    content = copy.deepcopy(_OVERFLOW_CONTENT)
    content["projects"][0]["bullets"] = content["projects"][0]["bullets"][:3]
    lines = _lines_for(content, n=2)
    out = page_line_budget(content, lines, budget=0)  # everything must go
    got = [(h["action"], h.get("bullet", h["item"])) for h in out["cut_hints"]]
    assert got == _ladder(copy.deepcopy(content))
    assert ("drop_item", "proj:project 3") in got


def test_missing_line_count_is_an_error():
    with pytest.raises(ValueError, match="no rendered line count"):
        page_line_budget(_JAKE_CONTENT, {})


# ── integration: real compiles ────────────────────────────────────────────────

def _profile_bullets():
    from eval.profile_fixture import load_profile
    out = []
    for path in sorted((ROOT / "eval" / "profiles").glob("*.md")):
        out.extend(load_profile(path).bullets)
    return list(dict.fromkeys(b.strip() for b in out if b.strip()))


def _bullet_glyph_baselines(pdf_bytes):
    """`(page, y)` of every bullet glyph in the PDF, in reading order (pypdf)."""
    from pypdf import PdfReader

    out = []
    for pno, page in enumerate(PdfReader(io.BytesIO(pdf_bytes)).pages):
        def visit(text, cm, tm, font, size, pno=pno):
            if text.strip() == "•":
                out.append((pno, tm[4] * cm[1] + tm[5] * cm[3] + cm[5]))
        page.extract_text(visitor_text=visit)
    return out


@pytest.mark.integration
def test_measured_lines_match_the_rendered_pdf():
    """Independent check: vertical space each bullet takes in the formatter's
    own PDF, read from where the next bullet glyph lands.

    Consecutive items in one list sit (lines x 12pt + 2pt) apart, so every
    real bullet is followed by a one-word sentinel bullet in the same entry.
    This counts height, not text: a bullet that exactly fills its line renders
    a second, empty line (the space before `\\vspace{-2pt}` in `\\resumeItem`
    breaks onto it), and that line costs page space like any other.
    """
    if _no_latex_engine():
        pytest.skip("no LaTeX engine (tectonic/pdflatex) installed")
    # Real bullets from every eval profile, plus controlled 1/2/3-line ones.
    texts = _profile_bullets() + [("word " * n).strip() for n in (12, 30, 50)]
    measured = measure_lines(texts)
    assert measured[-3:] == [1, 2, 3]
    assert len(texts) > 100 and {1, 2}.issubset(set(measured))

    # The formatter's real experience block: 5 bullets and a sentinel per entry.
    agent = ResumeFormatterAgent.__new__(ResumeFormatterAgent)
    agent._style = None
    exps, owner = [], []  # owner[g] = index into texts of glyph g (None: sentinel)
    for start in range(0, len(texts), 5):
        chunk = list(range(start, min(start + 5, len(texts))))
        exps.append({"title": f"Role {start}", "company": "Co", "start_date": "2020",
                     "end_date": "2021", "location": "Remote",
                     "bullets": [texts[i] for i in chunk] + ["End."]})
        owner += chunk + [None]
    tex = (_JAKE_PREAMBLE + "\n\\begin{document}\n"
           + agent._build_tex_experiences(exps) + "\n\\end{document}\n")
    glyphs = _bullet_glyph_baselines(_compile_tex_to_pdf(tex))
    assert len(glyphs) == len(owner)

    checked, split = 0, 0
    for g, idx in enumerate(owner):
        if idx is None:
            continue
        (page, y), (next_page, next_y) = glyphs[g], glyphs[g + 1]
        if page != next_page:  # the item straddles a page break
            split += 1
            continue
        assert round((y - next_y - 2) / 12) == measured[idx], texts[idx]
        checked += 1
    assert checked + split == len(texts) and split < 10


# `_OVERFLOW_CONTENT` does not actually overflow under tectonic: it renders to
# one page with about 7 lines to spare. Three more experiences make it overflow
# for real.
_REAL_OVERFLOW = dict(_OVERFLOW_CONTENT, experiences=[
    {**e, "title": f"{e['title']} {suffix}"}
    for suffix in ("A", "B") for e in _OVERFLOW_CONTENT["experiences"]])


@pytest.mark.integration
def test_page_budget_predicts_the_real_page_count(isolated_engine, monkeypatch):
    if _no_latex_engine():
        pytest.skip("no LaTeX engine (tectonic/pdflatex) installed")
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    skill_lines = len(agent._get_skill_categories(None))

    results = {}
    for name, content in (("jake", _JAKE_CONTENT), ("overflow_fixture", _OVERFLOW_CONTENT),
                          ("real_overflow", _REAL_OVERFLOW)):
        lines = content_bullet_lines(content)
        assert bullet_line_violations(lines) == []
        est = page_line_budget(content, lines, skill_lines=skill_lines,
                               education_entries=0)
        pages = _pdf_page_count(_compile_tex_to_pdf(agent._build_tex(content)))
        assert (est["over_by"] <= 0) == (pages == 1), (name, est, pages)
        results[name] = (est, pages)

    jake, jake_pages = results["jake"]
    assert jake_pages == 1 and jake["cut_hints"] == []
    fixture, fixture_pages = results["overflow_fixture"]
    assert fixture_pages == 1 and fixture["over_by"] <= 0
    over, over_pages = results["real_overflow"]
    assert over_pages == 2 and over["over_by"] > 0
    assert sum(h["frees"] for h in over["cut_hints"]) >= over["over_by"]

    # Every bullet is now cached: a second pass never calls the measurer.
    def boom(texts):
        raise AssertionError("cache miss on an already-measured bullet")
    assert content_bullet_lines(_REAL_OVERFLOW, measurer=boom)
