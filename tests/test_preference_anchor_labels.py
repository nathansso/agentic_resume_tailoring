"""The labelled set for the preference anchor resolver (#296).

`eval/preference_anchor_labels/pairs.json` holds hand-labelled (subject, expected) rows over five of the
synthetic profiles in `eval/profiles/`. Each row says what the user means, written down before the
resolver was run, so a row the resolver gets wrong is a finding and not a thing to relabel.

Deterministic: no Jev, no recordings, no store. Items come from `anchors.build_items` on the parsed
profile (plus a row's own `add` overlay), so a row costs microseconds.

* **Hard gate:** zero wrong-node bindings, a row where the resolver binds an item key other than the
  one expected (or binds at all when the user meant something that is not one item). A wrong binding
  attaches a ban or a pin to the wrong item, so it is never tolerated; a miss is only a pending term.
* **Second gate:** recall on the `alias` rows is at least 0.9.
* **Reported, not gated:** per-category accuracy and the precision of `document`. Run with `-s` to
  print the table.
"""

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pytest

from eval.profile_fixture import load_profile
from harness import anchors
from harness.anchors import AMBIGUOUS, BOUND, DOCUMENT, MULTIPLE, PENDING

ROOT = Path(__file__).resolve().parent.parent
PAIRS_PATH = ROOT / "eval" / "preference_anchor_labels" / "pairs.json"
PROFILES_DIR = ROOT / "eval" / "profiles"
DOC = json.loads(PAIRS_PATH.read_text(encoding="utf-8"))
ROWS = DOC["rows"]
STATUS_LABELS = {AMBIGUOUS, PENDING, DOCUMENT, MULTIPLE}

# Every category the set is built to cover, with the fewest rows it may have.
CATEGORIES = {
    "exact_key": 5, "label": 5, "bare_name": 15, "role": 8, "role_at_phrase": 2, "company_alone": 4,
    "ambiguous": 6, "alias": 12, "alias_overreach": 2, "alias_candidate": 6, "absent": 10,
    "short_decoy": 15, "substring_decoy": 10, "section": 10, "style": 6, "scope": 6, "negation": 5,
    "multiple": 10, "and_in_name": 5, "doc_word_decoy": 10,
}
GATED_ALIAS_RECALL = 0.9


@lru_cache(maxsize=None)
def _profile(stem: str):
    return load_profile(PROFILES_DIR / f"{stem}.md")


def _items(row):
    f = _profile(row["profile"])
    add = row.get("add") or {}
    return anchors.build_items(
        skills=[*f.skills, *({"name": n} for n in add.get("skills", ()))],
        experiences=[*f.experiences, *add.get("experiences", ())],
        projects=[*f.projects, *add.get("projects", ())],
        education=[*f.education, *add.get("education", ())],
        achievements=[*f.achievements, *add.get("achievements", ())])


def _resolve(row):
    return anchors.resolve(_items(row), row["subject"])


def _correct(row, out) -> bool:
    want = row["expected"]
    if want in STATUS_LABELS:
        return out["status"] == want
    return out["status"] == BOUND and out["key"] == want


def _wrong_node(row, out) -> bool:
    """Bound to an item the user did not mean (any item at all, when they meant none or several)."""
    return out["status"] == BOUND and out.get("key") != row["expected"]


@lru_cache(maxsize=None)
def _scored():
    return [(row, _resolve(row)) for row in ROWS]


# ── the set ──────────────────────────────────────────────────────────────────

def test_the_set_is_commented_large_enough_and_drawn_from_about_five_profiles():
    assert DOC["_comment"] and DOC["version"] == 1
    assert len(ROWS) >= 150
    assert len({r["id"] for r in ROWS}) == len(ROWS)
    profiles = {r["profile"] for r in ROWS}
    assert 4 <= len(profiles) <= 6
    for stem in profiles:
        assert (PROFILES_DIR / f"{stem}.md").is_file(), stem
    for r in ROWS:
        assert {"id", "profile", "subject", "phrasing", "category", "expected"} <= set(r), r["id"]
        assert r["subject"].strip() and r["phrasing"].strip()
        assert "personal" not in r["profile"]


def test_every_category_is_covered():
    by_cat = Counter(r["category"] for r in ROWS)
    assert set(by_cat) == set(CATEGORIES), set(by_cat) ^ set(CATEGORIES)
    short = {c: n for c, n in by_cat.items() if n < CATEGORIES[c]}
    assert not short, short


def test_every_label_is_a_status_or_a_key_that_exists_in_its_rows_items():
    """A typo in a label would otherwise read as a resolver miss."""
    for r in ROWS:
        keys = {i["key"] for i in _items(r)}
        if r["expected"] in STATUS_LABELS:
            continue
        assert r["expected"] in keys, (r["id"], r["expected"])


def test_an_ambiguous_row_names_the_candidates_it_expects():
    for r in ROWS:
        if r["expected"] == AMBIGUOUS:
            assert len(r.get("candidates") or []) >= 2, r["id"]
            keys = {i["key"] for i in _items(r)}
            assert set(r["candidates"]) <= keys, r["id"]


# ── the gates ────────────────────────────────────────────────────────────────

def test_hard_gate_no_row_binds_the_wrong_node():
    wrong = [(r["id"], r["subject"], r["expected"], out) for r, out in _scored() if _wrong_node(r, out)]
    assert not wrong, "\n".join(map(str, wrong))


def test_second_gate_alias_recall_is_at_least_nine_in_ten():
    rows = [(r, out) for r, out in _scored() if r["category"] == "alias"]
    hits = sum(_correct(r, out) for r, out in rows)
    recall = hits / len(rows)
    assert recall >= GATED_ALIAS_RECALL, (
        recall, [(r["id"], r["subject"], out["status"]) for r, out in rows if not _correct(r, out)])


def test_an_ambiguous_row_that_is_found_ambiguous_lists_exactly_the_candidates_expected():
    for r, out in _scored():
        if r["expected"] == AMBIGUOUS and out["status"] == AMBIGUOUS:
            assert {c["key"] for c in out["candidates"]} == set(r["candidates"]), r["id"]


def test_a_bare_name_the_graph_holds_binds_whatever_else_is_added_to_the_graph():
    """The decoy rows with an overlay (Kafka beside Kafka Streams, Spark beside Spark Notebook)."""
    for r, out in _scored():
        if r["category"] == "substring_decoy" and r.get("add"):
            assert _correct(r, out), (r["id"], out)


# ── what is reported ─────────────────────────────────────────────────────────

def _report():
    by_cat = defaultdict(lambda: [0, 0])
    for r, out in _scored():
        by_cat[r["category"]][0] += _correct(r, out)
        by_cat[r["category"]][1] += 1
    doc_out = [(r, out) for r, out in _scored() if out["status"] == DOCUMENT]
    doc_precision = sum(r["expected"] == DOCUMENT for r, _ in doc_out) / max(len(doc_out), 1)
    doc_rows = [(r, out) for r, out in _scored() if r["expected"] == DOCUMENT]
    doc_recall = sum(out["status"] == DOCUMENT for _, out in doc_rows) / max(len(doc_rows), 1)
    pend_rows = [(r, out) for r, out in _scored() if r["expected"] == PENDING]
    misfires = [r["id"] for r, out in pend_rows if out["status"] == DOCUMENT]
    total = sum(c for c, _ in by_cat.values()), len(ROWS)
    return by_cat, doc_precision, doc_recall, misfires, total


def test_per_category_accuracy_and_document_precision_are_reported(capsys):
    by_cat, doc_precision, doc_recall, misfires, (hit, n) = _report()
    with capsys.disabled():
        print(f"\n  anchor labels: {hit}/{n} correct, {sum(_wrong_node(r, o) for r, o in _scored())} wrong-node")
        for cat in CATEGORIES:
            c, t = by_cat[cat]
            print(f"    {cat:<16}{c:>3}/{t:<3}  {c / t:5.2f}")
        print(f"    document precision {doc_precision:.2f}  recall {doc_recall:.2f}  "
              f"pending read as document: {len(misfires)} {misfires}")
    assert hit <= n


# ── the two rules the set made tighter (#296) ────────────────────────────────

def _held(**kw):
    return anchors.build_items(**kw)


@pytest.mark.parametrize("subject", [
    "Page Rank", "page rank", "Style Transfer", "my order-routing startup", "Voice Assistant",
    "Page Object Model", "Summary Statistics", "Margin Trading", "Section 508", "Bullet Journal",
    "Landing Page Optimization",
])
def test_a_name_with_a_document_word_and_a_word_that_names_a_thing_is_not_a_document(subject):
    assert anchors.resolve([], subject)["status"] == PENDING


@pytest.mark.parametrize("subject", [
    "page", "one page", "keep it to one page", "2 pages", "one-page", "formal tone", "active voice",
    "resume length", "page count", "the order of my sections", "wording of the summary", "past tense",
    "the projects section", "bullets per role", "margins", "formatting", "summary",
])
def test_a_phrase_of_one_document_word_and_frame_words_is_a_document(subject):
    assert anchors.resolve([], subject)["status"] == DOCUMENT


def test_a_held_item_named_with_a_document_word_still_binds_before_the_document_rule():
    items = _held(projects=[{"name": "Page Rank"}], skills=[{"name": "Style Transfer"}])
    assert anchors.resolve(items, "Page Rank") == {"status": BOUND, "subject": "Page Rank",
                                                   "key": "proj:page rank", "step": "name"}
    assert anchors.resolve(items, "style transfer")["key"] == "skill:style transfer"


def test_two_section_names_are_one_document_subject_and_not_two_things_to_split():
    assert anchors.resolve([], "projects and skills")["status"] == DOCUMENT


def test_torchvision_is_not_a_spelling_of_pytorch_but_torch_is():
    items = _held(skills=[{"name": "PyTorch"}])
    assert anchors.resolve(items, "torch")["key"] == "skill:pytorch"
    assert anchors.resolve(items, "torchvision")["status"] == PENDING
    assert anchors.resolve(items, "torchnet")["status"] == PENDING


def test_a_message_naming_torchvision_does_not_name_pytorch():
    items = _held(skills=[{"name": "PyTorch"}])
    assert anchors.mentions(items, "never mention torchvision")["status"] == PENDING
    assert anchors.mentions(items, "never mention torch")["key"] == "skill:pytorch"
    assert anchors.mentions(items, "never mention PyTorch or torchvision")["key"] == "skill:pytorch"
