"""The landing site's demo (issues #275, #337): a replay of a real run, the target role, and a
before/after resume.

Hermetic: reads the committed files and never runs ART, npm, tectonic or the network. The
files are written by `scripts/build_site_demo.py` from a real run; these tests hold them to
what the page promises:

- the transcript is well-formed, plays for 60 to 90 seconds, and every tool in it is a tool
  of the real contract (the source `test_site_docs.py` uses);
- nothing in the profile, the posting, the transcript or the images' data names the owner,
  or carries a local path;
- the images exist, are small, and the page stays well under its 2 MB budget;
- both resume pages are whole pages that fill the sheet (their last line is in the bottom tenth),
  the posting card lists 4 to 6 requirements, and the two versions really differ: an item added
  from the graph, a cut, a move and a rewording, each tied to a requirement the posting makes;
- the page says the demo is a replay of a real run, and what was written by hand;
- the build script keeps a real user's data out of the run (scratch directory, no keys).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from harness.contract import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DEMO = SITE / "src" / "components" / "demo"
TRANSCRIPT = DEMO / "transcript.json"
BEFORE_AFTER = DEMO / "before-after.json"
PUBLIC = SITE / "public"
SCRIPTS = ROOT / "scripts"
DEMO_DATA = SCRIPTS / "site_demo"

PAGE_BUDGET = 2_000_000          # bytes, the issue's "under 2 MB before any video plays"
IMAGE_BUDGET = 600_000           # the two resume images, every width


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


T = _load(TRANSCRIPT)
BA = _load(BEFORE_AFTER)


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DEMO_DATA))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(DEMO_DATA))
    return mod


BUILD = _module("build_site_demo", SCRIPTS / "build_site_demo.py")


def _git_user_name() -> str:
    try:
        return subprocess.run(["git", "config", "user.name"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


# --- the transcript ---------------------------------------------------------------------

def test_transcript_is_well_formed():
    assert set(T) == {"meta", "phases", "events"}
    phase_ids = [p["id"] for p in T["phases"]]
    assert phase_ids == ["setup", "posting", "plan", "approve", "gates", "render"]
    assert all(p["label"] for p in T["phases"])
    assert len(T["events"]) >= 20
    seen = []
    for ev in T["events"]:
        assert ev["type"] in {"user", "agent", "tool"}, ev
        assert ev["phase"] in phase_ids, ev
        assert isinstance(ev["ms"], int) and ev["ms"] > 0
        if ev["type"] == "tool":
            assert ev["name"] and "args" in ev and "result" in ev, ev
        else:
            assert ev["text"].strip(), ev
        if not seen or seen[-1] != ev["phase"]:
            seen.append(ev["phase"])
    assert seen == phase_ids, "the phases must run in order, each once"


def test_the_run_follows_setup_then_posting_then_tailor_then_render():
    names = [e["name"] for e in T["events"] if e["type"] == "tool"]
    user = [e["text"] for e in T["events"] if e["type"] == "user"]
    assert user[0].startswith("/art:setup") and user[1].startswith("/art:tailor")
    for needed in ("upsert_items", "update_profile", "open_job", "execute_plan", "render"):
        assert needed in names, needed
    assert names.index("upsert_items") < names.index("open_job") < names.index("render")
    assert names.count("render") == 2, "one render for each side of the before/after"


def test_every_tool_in_the_transcript_is_in_the_real_contract():
    for ev in T["events"]:
        if ev["type"] == "tool":
            assert ev["name"] in BY_NAME, f"{ev['name']} is not a tool in harness/contract.py"


def test_every_slash_command_in_the_transcript_exists_in_the_plugin():
    plugin = {p.stem for p in (ROOT / "plugin" / "commands").glob("*.md")}
    said = {m for e in T["events"] if e["type"] == "user" for m in re.findall(r"/art:([a-z]+)", e["text"])}
    assert said and said <= plugin


def test_playback_lasts_sixty_to_ninety_seconds_and_the_timings_are_the_formula():
    total = sum(e["ms"] for e in T["events"])
    assert total == T["meta"]["total_ms"]
    assert 60_000 <= total <= 90_000, total
    for ev in T["events"]:
        assert ev["ms"] == BUILD.duration_ms(ev), "transcript.json was edited by hand; rebuild it"


def test_the_gate_rejection_is_real_and_is_disclosed():
    """The first draft carries an unsupported figure; ART reverts it and the page says why."""
    tools = [e for e in T["events"] if e["type"] == "tool" and e["name"] == "execute_plan"]
    rejected = [t for t in tools if '"status": "reverted"' in json.dumps(t["result"])]
    assert rejected, "no reverted node in the transcript"
    assert "consistency" in json.dumps(rejected[0]["result"])
    assert any(t["result"].get("committed") is True for t in tools[1:])
    notes = [e.get("note", "") for e in T["events"]]
    assert any("Written for this demo" in n for n in notes), "the staged slip must be labelled"


def test_meta_says_what_was_captured_and_what_was_written():
    meta = T["meta"]
    assert meta["label"] == "Replay of a real ART run on a sample student profile"
    assert "written by hand" in meta["authored"] and "captured" in meta["authored"]
    assert "ellipsis" in meta["elision"]
    assert "fixed rate" in meta["pacing"]
    assert meta["latex_engine"].lower().startswith(("tectonic", "pdflatex", "pdftex"))
    assert meta["jev"].startswith("off")


def test_the_ats_style_score_is_not_on_the_page():
    """It is reported, never promised or optimised (docs/harness.md section 5)."""
    blob = json.dumps(T).lower()
    assert '"ats"' not in blob and "ats-style" not in blob.replace("the ats-style report left out", "")


# --- no real person -----------------------------------------------------------------------

def _identifiers() -> list[str]:
    ids = ["nathaniel", "oliver", "noliver", "noliv", "nathansso", "ucsd"]
    ids += [part.lower() for part in _git_user_name().split() if len(part) > 2]
    return sorted(set(ids))


def test_the_profile_posting_and_replay_carry_no_owner_identifier():
    files = [DEMO_DATA / "sample_resume.md", DEMO_DATA / "sample_repos.md",
             DEMO_DATA / "sample_posting.txt", DEMO_DATA / "demo_script.py",
             TRANSCRIPT, BEFORE_AFTER, DEMO / "SessionReplay.astro", DEMO / "BeforeAfter.astro"]
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for ident in _identifiers():
            assert ident not in text, f"{path.relative_to(ROOT)} contains {ident!r}"


def test_the_sample_profile_is_obviously_synthetic():
    resume = (DEMO_DATA / "sample_resume.md").read_text(encoding="utf-8")
    assert "@example.com" in resume and "(555) 010-" in resume
    posting = (DEMO_DATA / "sample_posting.txt").read_text(encoding="utf-8")
    assert "sample posting" in posting.lower()


def test_no_local_path_or_machine_detail_reaches_the_page():
    for path in (TRANSCRIPT, BEFORE_AFTER):
        text = path.read_text(encoding="utf-8")
        for pattern in (r"[A-Za-z]:\\\\", r"[A-Za-z]:/Users", r"/Users/", r"/home/", "AppData",
                        r"art_site_demo_", r"\bTEMP\b"):
            assert not re.search(pattern, text), f"{path.name}: {pattern}"
    assert "~/.art/applications/" in json.dumps(T)


def _bullets_of(records) -> list[str]:
    return [b for r in records for b in r["data"].get("bullets", []) + r["data"].get("blurbs", [])]


def test_the_records_the_agent_stores_are_in_the_sample_sources_word_for_word():
    script = _module("site_demo_script", DEMO_DATA / "demo_script.py")
    resume = (DEMO_DATA / "sample_resume.md").read_text(encoding="utf-8")
    repos = (DEMO_DATA / "sample_repos.md").read_text(encoding="utf-8")
    for bullet in _bullets_of(script.RESUME_RECORDS):
        assert f"- {bullet}" in resume, bullet
        assert f"- {bullet}" not in repos
    for bullet in _bullets_of(script.REPO_RECORDS):
        assert f"- {bullet}" in repos, bullet
        assert f"- {bullet}" not in resume, "a repo project must not also be on the resume"
    for name, _ in script.RESUME_SKILLS:
        assert name in resume, name
    for name, _ in script.REPO_SKILLS:
        assert name in repos and name not in resume, name
    assert len(script.REPO_RECORDS) == 3 and all(r["kind"] == "project" for r in script.REPO_RECORDS)


def test_every_figure_in_the_plan_is_in_a_source_bullet_and_the_staged_one_is_not():
    """The plan only restates numbers the sources state, except the one staged to be rejected
    (SLIP_FIGURE), which the consistency gate must catch."""
    script = _module("site_demo_script", DEMO_DATA / "demo_script.py")
    sources = " ".join(_bullets_of(script.RESUME_RECORDS + script.REPO_RECORDS))
    plan = (script.KESTREL_BULLETS + script.TA_BULLETS + script.AB_BULLETS + script.CHURN_BULLETS
            + script.RETAIL_BULLETS)
    for text, _ in plan:
        for number in re.findall(r"\d[\d,.]*", text):
            assert number.rstrip(".,") in sources, (number, text)
    assert script.SLIP_FIGURE not in sources
    assert script.SLIP_FIGURE in script.AB_RESULT_SLIP and script.SLIP_FIGURE not in script.AB_RESULT
    # Every plan bullet cites a source bullet.
    assert all(starts for _, starts in plan)


# --- images and weight --------------------------------------------------------------------

def _image_files() -> list[Path]:
    paths = []
    for side in ("before", "after"):
        for src in BA[side]["sources"]:
            paths.append(PUBLIC / src["src"].lstrip("/"))
    return paths


def test_the_before_and_after_images_exist_and_are_web_formats():
    paths = _image_files()
    assert len(paths) == 4
    for path in paths:
        assert path.is_file(), path
        assert path.suffix == ".webp"
        assert path.read_bytes()[:4] == b"RIFF" and path.read_bytes()[8:12] == b"WEBP"
    for side in ("before", "after"):
        widths = [s["width"] for s in BA[side]["sources"]]
        assert widths == sorted(widths, reverse=True) and len(set(widths)) == 2


def test_media_weight_is_small_and_the_page_is_under_budget():
    images = sum(p.stat().st_size for p in _image_files())
    assert images < IMAGE_BUDGET, images
    data = TRANSCRIPT.stat().st_size + BEFORE_AFTER.stat().st_size
    fonts = sum(p.stat().st_size for p in (PUBLIC / "fonts").glob("*.woff2"))
    sources = sum(p.stat().st_size for p in (SITE / "src").rglob("*") if p.is_file()
                  and p.suffix in {".astro", ".ts", ".css", ".json"} and p.name != "package-lock.json")
    # The built page ships the data inside its HTML once, plus the images lazily, plus fonts.
    assert images + data * 2 + fonts + sources < PAGE_BUDGET, (images, data, fonts, sources)
    assert not list(PUBLIC.rglob("*.mp4")) and not list(PUBLIC.rglob("*.webm"))


def _last_ink_row(path: Path) -> float:
    """The bottom edge of the last row with dark pixels, as a fraction of the page height."""
    from PIL import Image

    with Image.open(path) as im:
        gray = im.convert("L")
        w, h = gray.size
        pixels = gray.load()
        for y in range(h - 1, -1, -1):
            if any(pixels[x, y] < 160 for x in range(0, w, 2)):
                return (y + 1) / h
    raise AssertionError(f"{path} is blank")


def test_both_pages_are_whole_and_full():
    """#337: no cropped, empty foot. The last line of text on each page sits in the bottom tenth,
    read from the images themselves and from the metric the build script records."""
    for side in ("before", "after"):
        big, small = BA[side]["sources"]
        # whole US Letter pages, not a crop
        for src in (big, small):
            assert abs(src["width"] / src["height"] - 8.5 / 11) < 0.01, (side, src)
            bottom = _last_ink_row(PUBLIC / src["src"].lstrip("/"))
            assert 0.90 <= bottom <= 0.985, (side, src["src"], bottom)
        assert 0.90 <= BA["fill"][side] <= 0.985, BA["fill"]
        assert abs(_last_ink_row(PUBLIC / big["src"].lstrip("/")) - BA["fill"][side]) < 0.01
    assert BUILD.MIN_FILL == 0.90
    assert "cropped_to" not in BA


def test_the_weight_check_and_the_ci_step_exist():
    assert (SITE / "scripts" / "check-weight.mjs").is_file()
    pkg = _load(SITE / "package.json")
    assert pkg["scripts"]["check:weight"] == "node scripts/check-weight.mjs"
    assert pkg["scripts"]["build"] == "astro build"
    workflow = (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")
    assert "npm run check:weight" in workflow


def test_highlights_are_real_boxes_computed_from_the_diff():
    assert BA["label"] == "Replay of a real ART run on a sample student profile"
    assert BA["counts"]["removed"] == len(BA["before"]["marks"]) > 0
    assert BA["counts"]["added"] == len(BA["after"]["marks"]) > 0
    for side in ("before", "after"):
        for m in BA[side]["marks"]:
            assert 0 <= m["x"] <= 1 and 0 <= m["y"] <= 1
            assert m["w"] > 0 and m["h"] > 0
            assert m["x"] + m["w"] <= 1.02 and m["y"] + m["h"] <= 1.02
    changes = BA["changes"]
    assert changes and sum(len(c["removed"]) for c in changes) == BA["counts"]["removed"]
    assert sum(len(c["added"]) for c in changes) == BA["counts"]["added"]
    # The tailored bullet the replay builds is among the changes.
    assert any("predict which subscribers cancel" in " ".join(c["added"]) for c in changes)


# --- the role and the difference (#337) --------------------------------------------------

def _terms(text: str) -> str:
    return text.lower().replace("-", " ")


def test_the_posting_card_shows_the_role_and_four_to_six_requirements():
    script = _module("site_demo_script", DEMO_DATA / "demo_script.py")
    card = BA["posting"]
    assert card["title"] == script.POSTING_TITLE and card["company"] == script.POSTING_COMPANY
    assert 4 <= len(card["requirements"]) <= 6
    posting = _terms((DEMO_DATA / "sample_posting.txt").read_text(encoding="utf-8"))
    assert _terms(script.POSTING_COMPANY) in posting and _terms(script.POSTING_TITLE) in posting
    assert "sample posting" in posting
    ids = [r["id"] for r in card["requirements"]]
    assert len(set(ids)) == len(ids)
    for req in script.REQUIREMENTS:
        assert any(_terms(t) in posting for t in req["terms"]), f"{req['id']}: none of its terms are in the posting"
    src = (DEMO / "BeforeAfter.astro").read_text(encoding="utf-8")
    assert "posting-heading" in src and "Sample posting, written for this demo" in src
    assert src.index("posting-heading") < src.index("<figure"), "the role comes before the pages"


def _entries(group_id: str) -> list[dict]:
    return [e for g in BA["groups"] if g["id"] == group_id for e in g["entries"]]


def test_the_demo_really_differs_and_each_change_names_a_requirement():
    script = _module("site_demo_script", DEMO_DATA / "demo_script.py")
    reqs = {r["id"]: r for r in script.REQUIREMENTS}
    assert [g["id"] for g in BA["groups"]] == ["added", "moved", "reworded", "cut", "skills"]
    assert [g["title"] for g in BA["groups"]] == ["Added from your graph", "Moved up", "Reworded", "Cut",
                                                  "Skills trimmed"]
    added, moved, reworded, cut = (_entries(g) for g in ("added", "moved", "reworded", "cut"))
    assert added and cut and moved and reworded
    removed_text = " ".join(t for c in BA["changes"] for t in c["removed"])
    added_text = " ".join(t for c in BA["changes"] for t in c["added"])
    for entry in added:
        # not on the usual page, on the tailored one
        assert entry["title"] in added_text and entry["title"] not in removed_text, entry["title"]
        assert entry["serves"], entry["title"]
    for entry in cut:
        if entry["scope"] == "item":
            head = entry["title"].split(",")[0]
            assert head in removed_text and head not in added_text, entry["title"]
        else:
            assert entry["text"] in removed_text and entry["text"] not in added_text
    first = next(e for e in added if e["position"] == 1)
    assert first["kind"] == "project", "an item from the graph leads the tailored projects"
    assert any(e["scope"] == "section" for e in moved), "Projects moved above Experience"
    for entry in moved:
        if entry["scope"] == "bullet":
            assert entry["to"] < entry["from"]
    # Every requirement a change names is one the posting card lists, and for a reworded or
    # added item the requirement's own terms appear in the text that changed.
    for g in BA["groups"]:
        for e in g["entries"]:
            for s in e.get("serves", []):
                assert s["id"] in reqs and s["short"] == reqs[s["id"]]["short"], s
    for e in reworded:
        assert e["before"] != e["after"]
        text = _terms(e["after"])
        assert any(_terms(t) in text for s in e["serves"] for t in reqs[s["id"]]["terms"]), e
    for e in added:
        text = _terms(" ".join(e["bullets"]))
        assert any(_terms(t) in text for s in e["serves"] for t in reqs[s["id"]]["terms"]), e["title"]
    skills = _entries("skills")[0]
    assert skills["after_count"] < skills["before_count"] and skills["added"] and skills["removed"]
    assert set(skills["first"]) >= {"Python", "SQL"}


def test_nothing_on_the_page_says_it_is_cropped_or_quotes_an_ats_score():
    assert "cropped" not in INDEX.lower() and "cropped" not in (DEMO / "BeforeAfter.astro").read_text().lower()
    assert "ats" not in re.findall(r"[a-z]+", json.dumps(BA).lower())


def test_build_groups_reads_the_changes_from_the_two_versions():
    """The grouping is pure: two versions' contents, ART's diff, the source bullets and the plan."""
    before = {"experiences": [{"title": "T", "company": "C", "bullets": ["a one", "b two", "c three"], "cites": {}}],
              "projects": [{"name": "Old", "bullets": ["old bullet"], "cites": {}}],
              "skills_ranked": [{"name": "Java"}, {"name": "Python"}]}
    after = {"experiences": [{"title": "T", "company": "C", "bullets": ["c three, with SQL", "a one"],
                              "cites": {"c three, with SQL": ["exp:t|c#b2"]}}],
             "projects": [{"name": "New", "bullets": ["new thing"], "cites": {"new thing": ["proj:new#b0"]}}],
             "skills_ranked": [{"name": "Python"}, {"name": "SQL"}]}
    sources = {"exp:t|c": ["a one", "b two", "c three"], "proj:new": ["new thing"], "proj:old": ["old bullet"]}
    diff = {"items": [
        {"key": "exp:t|c", "change": "revised", "bullets_added": ["c three, with SQL"],
         "bullets_removed": ["b two", "c three"]},
        {"key": "proj:new", "change": "added", "bullets_added": ["new thing"], "bullets_removed": []},
        {"key": "proj:old", "change": "removed", "bullets_added": [], "bullets_removed": ["old bullet"]}],
        "skills_added": ["SQL"], "skills_removed": ["Java"]}
    nodes = [{"id": "n1", "op": "revise", "item_key": "exp:t|c", "keywords": ["SQL"]},
             {"id": "n2", "op": "replace", "item_key": "proj:old", "replacement_key": "proj:new", "keywords": ["SQL"]}]
    reqs = [{"id": "sql", "short": "SQL", "terms": ["SQL"], "criticality": 5}]
    inp = {"before": before, "after": after, "diff": diff, "sources": sources, "nodes": nodes}
    groups = {g["id"]: g["entries"] for g in BUILD.build_groups(inp, reqs, {"default": "not asked for"})}
    assert groups["added"][0]["title"] == "New" and groups["added"][0]["replaces"] == "Old"
    assert groups["added"][0]["ordinal"] == "first"
    assert [(e["from"], e["to"]) for e in groups["moved"]] == [(3, 1)]
    assert groups["moved"][0]["serves"] == [{"id": "sql", "short": "SQL"}]
    assert groups["reworded"][0]["before"] == "c three" and groups["reworded"][0]["after"] == "c three, with SQL"
    cut = {(e["scope"], e.get("text") or e["title"]): e for e in groups["cut"]}
    assert ("bullet", "b two") in cut and ("item", "Old") in cut
    assert cut[("item", "Old")]["replaced_by"] == "New"
    assert groups["skills"][0]["added"][0]["name"] == "SQL" and groups["skills"][0]["removed"] == ["Java"]
    # A page that disagrees with ART's own diff is an error, not a quiet difference.
    try:
        BUILD.build_groups({**inp, "diff": {**diff, "skills_added": ["Rust"]}}, reqs, {"default": "x"})
    except AssertionError:
        pass
    else:
        raise AssertionError("build_groups accepted a diff that does not match the pages")



# --- the page ---------------------------------------------------------------------------

INDEX = (SITE / "src" / "pages" / "index.astro").read_text(encoding="utf-8")
REPLAY = (DEMO / "SessionReplay.astro").read_text(encoding="utf-8")
BEFORE_AFTER_SRC = (DEMO / "BeforeAfter.astro").read_text(encoding="utf-8")


def test_the_home_page_says_it_is_a_replay_of_a_real_run_with_a_sample_profile():
    assert "replay of a real ARTie run on a sample student profile" in INDEX
    assert "Neither is a real person or a real company" in INDEX
    assert "<SessionReplay />" in INDEX and "<BeforeAfter />" in INDEX
    assert 'id="demo-heading"' in INDEX and 'aria-labelledby="demo-heading"' in INDEX
    assert "written for this demo" in REPLAY and "captured from the run" in REPLAY


def test_the_replay_is_text_in_the_page_and_respects_reduced_motion():
    assert "prefers-reduced-motion" in REPLAY
    assert "matchMedia" in REPLAY and "reduced" in REPLAY
    assert "{ev.text}" in REPLAY, "steps must be rendered into the HTML, not built by script"
    for control in ("data-play", "data-restart", "data-all"):
        assert control in REPLAY
    assert 'aria-live="polite"' in REPLAY
    assert 'tabindex="0"' in REPLAY, "the scrolling transcript must be keyboard reachable"
    assert "IntersectionObserver" in REPLAY


def test_images_have_alt_text_lazy_loading_and_dimensions():
    tag = re.search(r"<img\b.*?/>", BEFORE_AFTER_SRC, re.S).group(0)
    for needle in ('alt={alt}', 'loading="lazy"', "width={big.width}", "height={big.height}", "srcset="):
        assert needle in tag, needle
    assert "Read every changed line as text" in BEFORE_AFTER_SRC, "highlights need a text alternative"
    assert "md:grid-cols-2" in BEFORE_AFTER_SRC, "side by side on desktop, stacked on mobile"


def test_the_demo_obeys_the_home_page_copy_rules():
    text = (INDEX + REPLAY + BEFORE_AFTER_SRC + TRANSCRIPT.read_text(encoding="utf-8")).lower()
    for banned in ("open source", "open-source", "sign up", "sign-up", "hosted", "guarantee",
                   "beat the ats", "railway", "/register", "testimonial"):
        assert banned not in text, banned
    for planned in ("record_feedback", "check_draft", "semantic duplicate", "eligibility rule",
                    "action ranking"):
        assert planned not in text, planned


# --- the build script -------------------------------------------------------------------

def test_the_build_script_runs_in_a_scratch_directory_with_no_keys(monkeypatch, tmp_path):
    for var in ("DATABASE_URL", "TYPESAFE_API_KEY", "ART_MCP_USER_ID", "ART_DATA_DIR",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SUPABASE_URL"):
        monkeypatch.setenv(var, "leak")
    env = BUILD.clean_env(tmp_path / "data", tmp_path / "bin")
    for var in ("DATABASE_URL", "TYPESAFE_API_KEY", "ART_MCP_USER_ID", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "SUPABASE_URL"):
        assert var not in env, var
    assert env["ART_DATA_DIR"] == str(tmp_path / "data")
    assert env["ART_JEV_MODE"] == "off"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["PATH"].startswith(str(tmp_path / "bin"))
    source = (SCRIPTS / "build_site_demo.py").read_text(encoding="utf-8")
    assert "home_art" in source and "mkdtemp" in source, "the data directory must be a new temp dir"


def test_shrinking_marks_what_it_drops_and_never_rewrites_a_value():
    shrunk = BUILD.shrink({"a": list(range(10)), "s": "x" * 400, "n": 3, "ok": True}, items=3, chars=50)
    assert shrunk["a"] == [0, 1, 2, "… 7 more"]
    assert shrunk["s"].endswith("…") and len(shrunk["s"]) <= 50
    assert shrunk["n"] == 3 and shrunk["ok"] is True
    assert BUILD.pick({"a": 1, "b": 2, "c": 3}, ["a"]) == {"a": 1, "…": "2 more fields"}
    assert BUILD.pick({"a": 1}, ["a"]) == {"a": 1}


def test_the_scratch_data_directory_is_shown_as_the_default_one():
    data = Path("C:/Users/someone/AppData/Local/Temp/art_site_demo_x/art_data")
    out = BUILD.sanitize({"p": f"{data}\\applications\\Co_Role\\resume.pdf", "n": 1}, data)
    assert out == {"p": "~/.art/applications/Co_Role/resume.pdf", "n": 1}


def test_the_readme_documents_the_refresh():
    readme = (SITE / "README.md").read_text(encoding="utf-8")
    assert "scripts/build_site_demo.py" in readme
    assert "replay" in readme.lower() and "tectonic" in readme
    assert "check:weight" in readme
    assert "sample_repos.md" in readme and "usual" in readme
    assert (SCRIPTS / "build_site_demo.py").is_file()
