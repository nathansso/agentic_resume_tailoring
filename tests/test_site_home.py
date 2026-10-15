"""The landing site's home page copy (issue #273).

Hermetic: reads the ``.astro`` sources under ``site/src`` and never calls npm or
the network. It guards the page's structure (one h1, the CTAs, the anchored
"how it works" section) and the copy rules in the root CLAUDE.md: no claim about
open-source licensing, a hosted app, a sign-up flow, or an ATS score.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent.parent / "site" / "src"
INDEX = SITE / "pages" / "index.astro"
HOME_COMPONENTS = sorted((SITE / "components" / "home").glob("*.astro"))


def _home_source() -> str:
    """The page plus the components it owns, as one string."""
    return "\n".join(p.read_text(encoding="utf-8") for p in [INDEX, *HOME_COMPONENTS])


def _visible_copy(source: str) -> str:
    """Template text only: drop the frontmatter fence and HTML comments."""
    body = re.sub(r"^---\n.*?\n---\n", "", source, count=1, flags=re.S)
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


def test_home_page_exists() -> None:
    assert INDEX.is_file()


def test_exactly_one_h1() -> None:
    assert len(re.findall(r"<h1[\s>]", INDEX.read_text(encoding="utf-8"))) == 1


def test_hero_headline_and_ctas() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "Land your first role" in src and "with ARTie" in src
    primary = re.search(r'<a\s+href="/install"[^>]*>\s*Install in Claude Code\s*</a>', src)
    assert primary, "the primary CTA must link to /install"
    secondary = re.search(r'<a\s+href="#how-it-works"[^>]*>\s*How it works\s*</a>', src)
    assert secondary, "the secondary CTA must link to #how-it-works"


def test_how_it_works_anchor_exists_once() -> None:
    assert INDEX.read_text(encoding="utf-8").count('id="how-it-works"') == 1


def test_closing_cta_links_to_install() -> None:
    # Hero, the requirements section, and the closing band each link to /install.
    assert INDEX.read_text(encoding="utf-8").count('href="/install"') >= 3


def test_install_cta_is_the_only_call_to_action_target() -> None:
    """No account flow: every internal link goes to /install or an in-page anchor."""
    hrefs = re.findall(r'href="([^"]+)"', INDEX.read_text(encoding="utf-8"))
    assert hrefs, "expected links on the home page"
    for href in hrefs:
        assert href == "/install" or href.startswith("#"), href


@pytest.mark.parametrize(
    "banned",
    [
        "open source",
        "open-source",
        "sign up",
        "sign-up",
        "signup",
        "hosted",
        "guarantee",
        "beat the ats",
        "railway",
        "/register",
        "testimonial",
    ],
)
def test_no_banned_phrases(banned: str) -> None:
    assert banned not in _home_source().lower()


def test_no_ats_percentage_claim() -> None:
    text = _home_source().lower()
    assert not re.search(r"\d+\s*%\s*ats", text)
    assert not re.search(r"ats\s*(score\s*)?(of|:)?\s*\d+\s*%", text)


def test_no_invented_numbers() -> None:
    """No statistics or user counts: the page makes no quantified claim."""
    src = INDEX.read_text(encoding="utf-8")
    copy = re.sub(r"<[^>]*>", " ", _visible_copy(src))  # text nodes, not class names
    copy += " " + " ".join(re.findall(r'(?:a|body|title|description):\s*"([^"]*)"', src))
    assert not re.search(r"\d+\s*%", copy)
    assert not re.search(r"\b\d[\d,]*\+?\s+(students|users|resumes|offers|interviews)\b", copy, re.I)


def test_planned_features_are_not_described_as_available() -> None:
    """#252 is planned, not shipped (docs/harness.md section 15)."""
    text = _home_source().lower()
    assert "record_feedback" not in text
    assert "check_draft" not in text


def test_how_it_works_names_the_five_harness_steps() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for needle in (
        "knowledge graph",
        "posting",
        "approved bullets",
        "gates",
        "LaTeX PDF",
    ):
        assert needle in src, needle
    # Only gates the docs list: citations, numbers/names, pins and bans, page fit.
    assert "No fabrication" in src
    assert "Your pins and bans" in src
    assert "One page" in src


def test_privacy_statement_is_local_first_and_accurate_about_jev() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "ARTie runs on your computer" in src
    assert "what your own agent sends to its model provider" in src
    # Jev is optional, keyed, and sends text; the page must say each of those.
    assert "TypeSafe API key" in src
    assert "ART_JEV_MODE=off" in src
    assert "nothing is sent" in src
    assert "Jev never writes resume text" in src


def _faq_answer(src: str, question: str) -> tuple[str, str]:
    """(a, more) of one FAQ entry, read from the frontmatter list."""
    entry = re.search(
        rf'q:\s*"{re.escape(question)}",\s*a:\s*"([^"]*)",(?:\s*more:\s*"([^"]*)",)?', src
    )
    assert entry, question
    return entry.group(1), entry.group(2) or ""


def test_api_key_faq_answers_no_first_then_offers_jev_as_optional() -> None:
    """#356: the key is an optional upgrade, never a step the page asks for."""
    answer, more = _faq_answer(INDEX.read_text(encoding="utf-8"), "Do I need an API key?")
    assert answer.startswith("No."), answer
    assert more.startswith("Optional: strengthen the checks with Jev."), more
    # What Jev adds is limited to what docs/harness.md section 4 supports.
    for claim in ("reworded claim", "cited source", "banned topic", "best approved bullet"):
        assert claim in more, claim
    # No Jev-vs-fallback numbers until #350 reports the measured fallbacks.
    assert not re.search(r"\d", more)


def test_privacy_note_leads_with_local_and_puts_jev_under_the_optional_heading() -> None:
    src = INDEX.read_text(encoding="utf-8")
    aside = src[src.index('aria-labelledby="privacy-heading"'):src.index("</aside>")]
    local = aside.index("Everything ARTie stores stays on your computer")
    optional = aside.index("Optional: strengthen the checks with Jev")
    assert local < optional
    # The Jev request wording lives under the optional heading, not before it.
    assert "TypeSafe" not in aside[:optional]
    assert "excerpts of your resume bullets" in aside[optional:]
    assert "ARTie works fully without this" in aside[optional:]


def test_requirements_are_named_honestly() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for needle in ("Claude Code or Codex", "uv", "tectonic or pdflatex"):
        assert needle in src, needle
    assert "free to use" in src.lower()
    assert "public on GitHub" in src


def test_no_fabrication_section_is_ported() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "It never invents experience you don't have" in src
    assert "What tailoring changes" in src
    assert "What it never changes" in src


def test_faq_covers_the_four_questions_in_details_elements() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "<details" in src and "<summary" in src
    for question in (
        "Does it write my resume from scratch?",
        "Do I need an API key?",
        "Where is my data stored?",
        "applicant tracking systems",
    ):
        assert question in src, question


def test_ats_answer_says_report_only_and_never_optimized() -> None:
    src = INDEX.read_text(encoding="utf-8")
    assert "never optimizes for it" in src
    assert "never promises one" in src
    assert "can't promise" in src


def test_pain_points_cover_the_student_cases() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for needle in ("never written a real resume", "what recruiters want", "One resume, fifty postings"):
        assert needle in src, needle


def test_images_and_icons_are_accessible() -> None:
    """Icons are decorative and aria-hidden; any <img> needs alt text."""
    for path in [INDEX, *HOME_COMPONENTS]:
        src = path.read_text(encoding="utf-8")
        for tag in re.findall(r"<img\b[^>]*>", src):
            assert "alt=" in tag, f"{path.name}: {tag}"
    icon = (SITE / "components" / "home" / "Icon.astro").read_text(encoding="utf-8")
    assert 'aria-hidden="true"' in icon


def test_sections_are_labelled_for_screen_readers() -> None:
    src = INDEX.read_text(encoding="utf-8")
    for heading_id in ("pain-heading", "how-heading", "grounded-heading", "needs-heading", "faq-heading", "cta-heading"):
        assert f'aria-labelledby="{heading_id}"' in src
        assert f'id="{heading_id}"' in src


def test_mobile_safe_layout_hooks() -> None:
    """16px gutter and no fixed widths wider than a phone outside the clipped hero glow."""
    src = INDEX.read_text(encoding="utf-8")
    assert src.count("px-4 sm:px-6") >= 5
    # The only wide fixed-width elements are the blurred glows inside overflow-hidden sections.
    for match in re.finditer(r'class="([^"]*\bw-\[(\d+)px\][^"]*)"', src):
        assert "blur-" in match.group(1), match.group(1)
