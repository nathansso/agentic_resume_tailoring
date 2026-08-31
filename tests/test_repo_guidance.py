"""One policy surface, guarded mechanically (issue #182).

`AGENTS.md` exists only because Codex reads that filename and does not fall back
to `CLAUDE.md`. It was written during #172 as a 230-line *copy* of root
`CLAUDE.md`, which the root file rules out in as many words: "There is
deliberately no second policy system."

The issue predicted drift "a matter of weeks" out. It had already happened by
the time this test was written — commit `03a9b26` added a `## Response style`
section to `CLAUDE.md` only, so a Codex session in this repo was reading stale
policy and nothing detected it. Hence a test rather than a convention: the copy
is cheap to re-paste and invisible when stale.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"

# Section headings that belong to root CLAUDE.md alone. Their presence in
# AGENTS.md means policy has been pasted back in rather than pointed at.
POLICY_HEADINGS = (
    "## Testing",
    "## Architecture invariants",
    "## Work tracking",
    "## GitHub project board",
)

# A pointer needs a paragraph or two. Anything past this is prose that will drift.
MAX_LINES = 25

_WHY = ("Root `CLAUDE.md` is the single policy surface for this repo (#182); "
        "`AGENTS.md` is a pointer to it because Codex does not read `CLAUDE.md`. "
        "Add the guidance to `CLAUDE.md` (or a folder-local `CLAUDE.md`) and "
        "leave this file pointing at it.")


def test_agents_md_is_committed():
    """Deleting it is not the fix — Codex would then read no guidance at all."""
    assert AGENTS.is_file(), (
        f"AGENTS.md is missing. {_WHY} Codex has no fallback to `CLAUDE.md`, so "
        "removing the file leaves those sessions with nothing.")


def test_agents_md_points_at_claude_md():
    assert "CLAUDE.md" in AGENTS.read_text(encoding="utf-8"), (
        f"AGENTS.md no longer names `CLAUDE.md`. {_WHY}")


def test_agents_md_stays_a_pointer_not_a_second_policy():
    text = AGENTS.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) <= MAX_LINES, (
        f"AGENTS.md has grown to {len(lines)} lines (budget {MAX_LINES}) — it is "
        f"regrowing into a second copy of the policy. {_WHY}")
    for heading in POLICY_HEADINGS:
        assert heading not in text, (
            f"AGENTS.md carries the `{heading}` section. {_WHY}")


def test_claude_md_documents_the_pointer_from_its_own_end():
    """A reader arriving at either file learns the same arrangement."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text, (
        "root CLAUDE.md no longer mentions `AGENTS.md`; the § Guidance hierarchy "
        "note explaining why that file exists is what stops the next author from "
        "either deleting it or filling it with policy (#182).")
