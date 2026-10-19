"""Preference compliance benchmark (issue #299): do rendered resumes obey what the user said?

Preferences and page rules are enforced by gates; this measures whether the gates hold. It is
offline and deterministic: no model, no network, a throwaway store, Jev off.

    python eval/preference_compliance.py --profiles 5 --jobs 6

**What is crossed.** `--profiles` of the synthetic profiles in `eval/profiles/` (the five the
anchor labels use, #296) x seeded preference sets x `--jobs` postings drawn round-robin from
the five role families (`eval/tailoring_benchmark.load_tasks`), each run through the scripted
host (`eval/scripted_host.py`): `execute_plan`, a commit, then the `render` tool's `.tex`.

**The seeded sets** (`SET_NAMES`): a hard suppress of an experience, a project and a skill; a
ban on a term the graph does not hold (pending); a role-family-scoped pin and a job-scoped pin
(each applies to its scope only); a soft emphasize and a soft suppress; the page rules (one
page, a section left off, a section order, bullets per item, a skills cap, and a narrower
scope that tries to loosen a hard one); and two superseded preferences (durability).

**Two hosts.**

- *naive* ignores preferences entirely: the scripted host's own plan, plus (for the pending
  ban only) a bullet that weaves the banned term in, as an eager host does. This measures
  **gate recall**: nothing that violates a hard preference or rule may reach the committed,
  rendered page. A run is a **trap** when the same plan on a store with no preferences (the
  *control*) renders a page the checker calls a violation; the gate must stop each one.
- *obedient* reads the "Active constraints for this job" block (`suggest_actions`, #299) and
  plans around it. This measures **over-block** (pages that lose things no in-scope preference
  asked for, and nodes the executor refused for a preference it was told about) and **scope
  leak** (an out-of-scope job that loses something it should have kept).

**The checker is independent** (`eval/compliance_checker.py`): it imports nothing from the
repository and reads the rendered `.tex`. The expectations it is given are written here, from
the seeded sets, in the user's terms (a title and company, a term, a section), never keys.

Metrics (targets): hard violations on the naive host 0; scope leak 0; over-block rate <= 2%;
durability pass rate 1.0. Not a gate in `run_tests.py`; a 1 profile x 2 jobs smoke run is
(`tests/test_preference_compliance.py`).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import compliance_checker as checker  # noqa: E402

PROFILES_DIR = ROOT / "eval" / "profiles"
RESULTS_DIR = ROOT / "eval" / "results"

# The five profiles #296's anchor labels use: one per role family, a mix of shapes.
PROFILES = (
    "benchmark_profile",
    "ml_engineering_specialist_sofia_marchetti",
    "software_engineering_generalist_devon_whitaker",
    "data_engineering_generalist_adaeze_nwosu",
    "data_science_specialist_priya_raman",
)
# The dataset's family labels, as the role family a host supplies to `open_job` (the harness's
# closed set). A host reads the posting and names its family; the benchmark's host does too.
FAMILY = {"software_engineering": "software_engineering", "data_engineering": "data_engineering",
          "data_science": "data_science", "ml_engineering": "machine_learning",
          "ai_engineering": "machine_learning"}
# Terms a pending ban names. The first is lowercase and outside the consistency gate's technology
# list (#296: the documented gap, so the page-wide term pin is the only thing in the way); the
# second is on the list (a gate the evidence checks already close).
TEMPT_LOWER = ("redpanda", "dagster", "temporal", "nats")
TEMPT_TECH = ("kafka", "rabbitmq", "cassandra", "terraform", "elasticsearch")
CHARS_PER_LINE = 100          # the stand-in line measurer when there is no LaTeX engine
SECTION_NAMES = ("education", "experience", "projects", "skills", "achievements")
PREF_REASONS = ("hard_preference", "negative_pin", "page_rule")


# ── the store ────────────────────────────────────────────────────────────────

def _measure(texts: Sequence[str]) -> List[int]:
    return [max(1, -(-len(t) // CHARS_PER_LINE)) for t in texts]


class _Env:
    """Settings a run needs and restores: Jev off, the deterministic measurer."""

    def __enter__(self):
        from harness import executor
        self._mode = os.environ.get("ART_JEV_MODE")
        os.environ["ART_JEV_MODE"] = "off"
        self._measurer = executor.MEASURER
        executor.MEASURER = _measure
        return self

    def __exit__(self, *exc):
        from harness import executor
        executor.MEASURER = self._measurer
        if self._mode is None:
            os.environ.pop("ART_JEV_MODE", None)
        else:
            os.environ["ART_JEV_MODE"] = self._mode


def has_latex_engine() -> bool:
    from agents.formatter import _find_latex_engine
    return _find_latex_engine() is not None


# ── tool calls ───────────────────────────────────────────────────────────────

def _call(name: str, uid, args: Dict) -> Dict:
    from harness.contract import invoke
    out = invoke(name, uid, args)
    if out.get("error"):
        raise RuntimeError(f"{name}: {out['error']}")
    return out


def open_job(uid, task: Dict) -> str:
    """`open_job` as a host does: the requirements it read, and the role family it named."""
    from eval.scripted_host import extract_requirements
    return _call("open_job", uid, {
        "jd_text": task["description"],
        "requirements": extract_requirements(task["description"]),
        "metadata": {"title": task["title"], "company": task["company"], "url": task.get("url"),
                     "role_family": FAMILY.get(task.get("role_family") or "")}})["job_id"]


_RUN = [0]          # a run counter, so two runs over one store never reuse an email


def _seed(fixture, tag: str):
    from eval.scripted_host import seed_profile
    return seed_profile(fixture, name="Benchmark Candidate",
                        email=f"run{_RUN[0]}.{tag}@example.com")


# ── hosts ────────────────────────────────────────────────────────────────────

def naive_program(uid, job_id: str, task: Dict, tempt: Sequence[str] = ()) -> Dict:
    """The scripted host's plan, which never reads a preference. `tempt` terms are woven into
    the first bullet of an experience each, the way an eager host works a posting's keywords
    in: the host does not know the user banned them."""
    from eval.scripted_host import build_program
    program = build_program(uid, job_id, task["description"])
    if not tempt:
        return program
    nodes = program["nodes"]
    exp = [i for i, n in enumerate(nodes) if n["item_key"].startswith("exp:")]
    for term, at in zip(tempt, exp):
        node = nodes[at]
        if node["op"] == "keep":
            record = _call("get_item", uid, {"key": node["item_key"]})["record"]
            node = {"id": f"weave:{node['item_key']}", "op": "revise", "item_key": node["item_key"],
                    "strategy": "keyword_weave",
                    "because": "job:weave in a term the posting uses",
                    "bullets": [{"text": b, "cites": [f"{node['item_key']}#b{j}"]}
                                for j, b in enumerate(record.get("bullets") or [])],
                    "accept": {"improves": ["relevance_density"]}}
            nodes[at] = node
        first = node["bullets"][0]
        first["text"] = f"{first['text'].rstrip('. ')}, using {term}."
    return program


def _word(term: str) -> "re.Pattern":
    t = re.sub(r"[\s_-]+", " ", term.strip().lower())
    return re.compile(r"(?<![a-z0-9])" + re.escape(t).replace(r"\ ", r"[\s_-]+") + r"(?![a-z0-9])")


def _says(text: str, terms: Sequence[str]) -> bool:
    low = text.lower()
    return any(_word(t).search(low) for t in terms if t)


def _page_keys(content: Dict) -> Dict[str, List[str]]:
    from agents.checks import exp_key, proj_key
    return {"exp": [exp_key(i) for i in content.get("experiences") or []],
            "proj": [proj_key(i) for i in content.get("projects") or []]}


def read_block(uid, job_id: str) -> Dict:
    """The reminder block a host reads before it writes: `suggest_actions`'s."""
    return _call("suggest_actions", uid, {"job_id": job_id})["active_constraints"]


def obedient_program(uid, job_id: str, task: Dict) -> Tuple[Dict, Dict]:
    """The plan of a host that reads the block first. Returns `(program, block)`.

    Reads only the block and the knowledge graph through the tools. Per constraint: an item it
    must not show is deleted (`because: pref:<id>`); a bullet that says a term it must not say
    is cut (`delete_bullet`) and the term is kept out of the skills list; an item it must keep
    is kept (a project it lacks is swapped in with `replace`); the page rules set the bullets
    per item, the skills cap and the sections left off; a section order is left to the rule's
    default. Everything else is the scripted host's policy."""
    from agents.ats_scorer import ATSScoringEngine
    from agents.skill_scorer import MAX_SKILLS
    from eval.scripted_host import HOST, KEEP_EXP_BULLETS, KEEP_PROJECTS, _overlap

    block = read_block(uid, job_id)
    jd_terms = ATSScoringEngine._extract_keywords(task["description"])
    head = _call("get_head", uid, {"job_id": job_id})["head"]
    items = _call("list_items", uid, {})["items"]
    on_page = None
    if head:
        pk = _page_keys(head["content"])
        on_page = set(pk["exp"]) | set(pk["proj"])

    suppress: Dict[str, str] = {}
    keep: Dict[str, str] = {}
    banned: List[str] = []
    ban_pref: Dict[str, str] = {}
    for e in ((block.get("hard_pins") or []) + (block.get("item_preferences") or [])
              + (block.get("pending_bans") or [])):
        key = (e.get("target_key") or "").strip().lower()
        if e.get("polarity") == "suppress":
            if key.startswith(("exp:", "proj:")):
                suppress[key] = e["preference_id"]
                continue
            term = key.split(":", 1)[1] if key.startswith("skill:") else e.get("target_term")
            if term:
                banned.append(term)
                ban_pref[term] = e["preference_id"]
        elif e.get("polarity") == "emphasize" and key.startswith(("exp:", "proj:")):
            keep[key] = e["preference_id"]

    per_item: Optional[int] = None
    skills_cap: Optional[int] = None
    off: set = set()
    rules = (block.get("rules") or {})
    rule_text: Dict[str, str] = {}
    for r in (rules.get("soft") or []) + (rules.get("hard") or []):   # hard last: it wins
        param, value = r.get("param"), r.get("value")
        if param == "bullets_per_item":
            per_item, rule_text[param] = value, r.get("text")
        elif param == "max_skills":
            skills_cap = value
        elif param in SECTION_NAMES and value == 0:
            off.add(param)
    # The section order needs nothing here: a hard or soft `section_order` rule is the default
    # order of a plan that sets none, and a plan that names its own must name every section.

    nodes: List[Dict] = []

    def record(key: str) -> Dict:
        return _call("get_item", uid, {"key": key})["record"]

    # An item every bullet of which says a term the user banned cannot be shown without it: it
    # leaves the page like a suppressed one, for the preference that bans the term.
    for it in items:
        if it["kind"] not in ("experience", "project") or it["key"] in suppress or not banned:
            continue
        r = record(it["key"])
        bullets = ([b["content"] for b in r.get("blurbs") or []] or
                   ([r["description"]] if r.get("description") else [])) \
            if it["kind"] == "project" else (r.get("bullets") or [])
        said = [next(t for t in banned if _says(b, [t])) for b in bullets if _says(b, banned)]
        if bullets and len(said) == len(bullets):
            suppress[it["key"]] = ban_pref[said[0]]

    def trim(key: str, bullets: List[str], limit: int, own_policy: bool) -> Optional[Dict]:
        """The node that leaves `key` with the bullets it should have, or None to leave it.
        A bullet the user's words rule out (a banned term, a per-item limit) is cut with
        `delete_bullet`, the requested delete (#289); the host's own top-N choice is a revise."""
        said = [j for j, b in enumerate(bullets) if _says(b, banned)]
        rest = [j for j in range(len(bullets)) if j not in said]
        ranked = sorted(rest, key=lambda j: (-_overlap(bullets[j], jd_terms), j))
        final = sorted(ranked[:limit])
        constrained = bool(said) or (per_item is not None and len(rest) > per_item)
        if not final or (len(final) == len(bullets)):
            return None
        if constrained:
            term = next((t for t in banned if any(_says(bullets[j], [t]) for j in said)), None)
            because = (f"pref:{ban_pref[term]}" if term else
                       f"user:{rule_text.get('bullets_per_item') or 'keep to the limit'}")
            cuts = [j for j in range(len(bullets)) if j not in final]
            return {"id": f"cut:{key}", "op": "delete_bullet", "item_key": key,
                    "cut_bullets": [f"{key}#b{j}" for j in cuts], "because": because}
        if not own_policy:
            return None
        return {"id": f"tighten:{key}", "op": "revise", "item_key": key, "strategy": "tighten",
                "because": "job:keep the bullets most relevant to the posting",
                "bullets": [{"text": bullets[j], "cites": [f"{key}#b{j}"]} for j in final],
                "accept": {"improves": ["relevance_density"]}}

    def exp_nodes() -> None:
        keys = [i["key"] for i in items if i["kind"] == "experience"
                and (on_page is None or i["key"] in on_page)]
        for key in keys:
            if key in suppress:
                if len(keys) - sum(1 for k in keys if k in suppress) >= 1:
                    nodes.append({"id": f"drop:{key}", "op": "delete", "item_key": key,
                                  "because": f"pref:{suppress[key]}"})
                continue
            bullets = record(key).get("bullets") or []
            limit = min(KEEP_EXP_BULLETS, per_item) if per_item else KEEP_EXP_BULLETS
            node = trim(key, bullets, limit, own_policy=True)
            nodes.append(node or {"id": f"keep:{key}", "op": "keep", "item_key": key,
                                  "because": "job:already fits the page"})

    def proj_nodes() -> None:
        pool = [i["key"] for i in items if i["kind"] == "project"]
        present = [k for k in pool if on_page is None or k in on_page]
        wanted = [k for k in pool if k not in suppress]
        recs = {k: record(k) for k in set(pool)}

        def text(k: str) -> str:
            r = recs[k]
            return " ".join([r.get("name") or "", r.get("description") or "",
                             *(b["content"] for b in r.get("blurbs") or [])])
        ranked = sorted(wanted, key=lambda k: (k not in keep, -_overlap(text(k), jd_terms), k))
        kept = ranked[:max(KEEP_PROJECTS, sum(1 for k in ranked if k in keep))]
        out = [k for k in present if k not in kept]
        incoming = [k for k in kept if on_page is not None and k not in on_page]
        # A project the page lacks takes the place of one it leaves (`replace`), never a slot of
        # its own: the page holds the host's two. A project the user suppressed is deleted, the
        # requested delete a guard cannot veto; it is never the one swapped out.
        free = [k for k in out if k not in suppress]
        # The only way to put a project on a page is to swap one off it, so a project the user
        # asked to keep that the page lacks takes the slot of the lowest-ranked one it has.
        spare = [k for k in reversed(kept) if k in present and k not in keep and k not in free]
        free += spare[:max(0, len(incoming) - len(free))]
        for old, new in zip(free, incoming):
            nodes.append({"id": f"swap:{new}", "op": "replace", "item_key": old,
                          "replacement_key": new,
                          "because": "user:bring back a project they asked to keep"})
        gone = len(present)
        for key in free[len(incoming):] + [k for k in out if k in suppress]:
            if gone <= 1:
                break
            gone -= 1
            nodes.append({"id": f"drop:{key}", "op": "delete", "item_key": key,
                          "because": f"pref:{suppress[key]}" if key in suppress
                          else f"user:keep the {KEEP_PROJECTS} most relevant projects"})
        for key in kept:
            if on_page is not None and key not in on_page:
                continue
            r = recs[key]
            bullets = [b["content"] for b in r.get("blurbs") or []] or (
                [r["description"]] if r.get("description") else [])
            node = trim(key, bullets, per_item or len(bullets), own_policy=False)
            if node:
                nodes.append(node)

    if "experience" not in off:
        exp_nodes()
    if "projects" not in off:
        proj_nodes()

    skills = [i["title"] for i in items if i["kind"] == "skill" and not _says(i["title"], banned)]
    skills.sort(key=lambda s: (-_overlap(s, jd_terms), s.lower()))
    limit = min(MAX_SKILLS, skills_cap) if skills_cap else MAX_SKILLS
    program = {"job_id": job_id, "parent": head["node_id"] if head else None, "nodes": nodes,
               "skills": skills[:limit], "host": {**HOST, "name": "obedient-host"}}
    return program, block


# ── scenarios: the seeded preference sets ────────────────────────────────────

@dataclass
class Scenario:
    """One seeded set. `steps` are the tool calls that store it (`scope_job` is a job index
    resolved to that job's id); `expect(j)` is the checker's expectation for job `j`, or None
    when the set does not bind there; `stage2` re-records over it and is judged again."""
    name: str
    group: str
    note: str
    steps: List[Dict]
    expect: Callable[[int], Optional[Dict]]
    soft: Callable[[int], Optional[Dict]] = lambda j: None      # units a soft preference asks for
    tempt: Tuple[str, ...] = ()
    needs_pages: bool = False
    stage2: Optional[Dict] = None


SET_NAMES = ("suppress_exp", "suppress_proj", "suppress_skill", "pending_ban", "family_pin",
             "job_pin", "soft", "rule_pages", "rule_exclude", "rule_order", "rule_bullets",
             "rule_skills", "rule_narrower", "supersede_allow", "supersede_forbid")


def _present_counts(controls: List[Optional[Dict]], kind: str) -> Dict[Tuple, List[int]]:
    """Where each item sits on the control pages: item identity -> job indexes it is on."""
    out: Dict[Tuple, List[int]] = {}
    for j, c in enumerate(controls):
        if not c:
            continue
        if kind == "experience":
            for it in c["inv"]["experiences"]:
                out.setdefault((it["title"], it["company"]), []).append(j)
        elif kind == "project":
            for it in c["inv"]["projects"]:
                out.setdefault((it["name"],), []).append(j)
    return out


def _item_target(kind: str, ident: Tuple) -> Dict:
    return ({"kind": "experience", "title": ident[0], "company": ident[1]} if kind == "experience"
            else {"kind": "project", "name": ident[0]})


def _pick(controls, kind: str, must_be_on: Sequence[int], prefer_on: Sequence[int] = (),
          need_peers: int = 2) -> Optional[Dict]:
    """The item present on every job in `must_be_on` and on the most of `prefer_on`, ties by name.
    Never an item that is the only one of its kind on a page (deleting it empties the section)."""
    counts = _present_counts(controls, kind)
    sizes = {j: len(c["inv"]["experiences" if kind == "experience" else "projects"])
             for j, c in enumerate(controls) if c}
    best = None
    for ident, jobs in sorted(counts.items()):
        if not all(j in jobs for j in must_be_on):
            continue
        if any(sizes.get(j, 0) < need_peers for j in must_be_on):
            continue
        score = (len(set(jobs) & set(prefer_on)), -len(ident[0]))
        if best is None or score > best[0]:
            best = (score, ident)
    return _item_target(kind, best[1]) if best else None


def _pick_skill(controls, profile_text: str) -> Optional[str]:
    """A skill on the most control pages, preferring one that bullets also say (so the term pin
    has bullets to catch, not only the list). Short names (R, Go, C) are left alone."""
    counts: Dict[str, List[int]] = {}
    said: Dict[str, int] = {}
    for j, c in enumerate(controls):
        if not c:
            continue
        bullets = " ".join(b for it in c["inv"]["experiences"] + c["inv"]["projects"]
                           for b in it["bullets"])
        for s in c["inv"]["skills"]:
            if len(s) >= 4:
                counts.setdefault(s, []).append(j)
                if checker.mentions(bullets, s):
                    said[s] = said.get(s, 0) + 1
    if not counts:
        return None
    return sorted(counts, key=lambda s: (-said.get(s, 0), -len(counts[s]), s.lower()))[0]


def build_scenarios(fixture, controls: List[Optional[Dict]], jobs: List[Dict],
                    only: Optional[Sequence[str]] = None) -> Tuple[List[Scenario], List[Dict]]:
    """The seeded sets for one profile, with their targets read off the control pages, and the
    sets that could not be built here (with why)."""
    n = len(jobs)
    everywhere = list(range(n))
    fam = [j["family"] for j in jobs]
    profile_text = json.dumps({"e": fixture.experiences, "p": fixture.projects,
                               "s": fixture.skills, "a": fixture.achievements}).lower()
    out: List[Scenario] = []
    skipped: List[Dict] = []

    def want(name: str) -> bool:
        return only is None or name in only

    def skip(name: str, why: str) -> None:
        skipped.append({"set": name, "reason": why})

    def hard_pref(text, target, polarity="suppress", strength=5, scope="global", scope_job=None,
                  scope_value=None):
        return {"tool": "record_preference",
                "args": {"text": text, "polarity": polarity, "target": target,
                         "strength": strength, "scope": scope, "scope_value": scope_value,
                         "quote": text},
                "scope_job": scope_job}

    def rule(text, kind, params, strength=5, scope="global", scope_value=None, expect_refused=False):
        return {"tool": "record_rule",
                "args": {"text": text, "kind": kind, "params": params, "strength": strength,
                         "scope": scope, "scope_value": scope_value, "quote": text},
                "expect_refused": expect_refused}

    def label(target: Dict) -> str:
        return target.get("title") and f"{target['title']} @ {target['company']}" or target["name"]

    def item_target_for(kind: str) -> Optional[Dict]:
        return _pick(controls, kind, must_be_on=[], prefer_on=everywhere)

    # item suppressions (global) ------------------------------------------------
    for name, kind in (("suppress_exp", "experience"), ("suppress_proj", "project")):
        if not want(name):
            continue
        t = item_target_for(kind)
        if t is None:
            skip(name, f"no {kind} sits on a control page with a peer")
            continue
        src = f"{name}: never include {label(t)}"
        out.append(Scenario(
            name, "item", f"hard suppress of one {kind}, global",
            [hard_pref(f"Never include {label(t)} on a resume.", label(t))],
            lambda j, t=t, src=src: {"absent_items": [{**t, "source": src}]}))

    if want("suppress_skill"):
        s = _pick_skill(controls, profile_text)
        if s is None:
            skip("suppress_skill", "no skill on a control page")
        else:
            src = f"suppress_skill: never mention {s}"
            out.append(Scenario(
                "suppress_skill", "item", "hard suppress of one skill, global",
                [hard_pref(f"Never mention {s} on a resume.", s)],
                lambda j, s=s, src=src: {"banned_terms": [{"term": s, "source": src}]}))

    if want("pending_ban"):
        lower = next((t for t in TEMPT_LOWER if t not in profile_text), None)
        tech = next((t for t in TEMPT_TECH if t not in profile_text), None)
        terms = tuple(t for t in (lower, tech) if t)
        if not terms:
            skip("pending_ban", "every candidate term is in the profile")
        else:
            out.append(Scenario(
                "pending_ban", "pending",
                "hard bans on terms the graph does not hold; the host weaves them in",
                [hard_pref(f"Never mention {t} on a resume.", t) for t in terms],
                lambda j, terms=terms: {"banned_terms": [
                    {"term": t, "source": f"pending_ban: never mention {t}"} for t in terms]},
                tempt=terms))

    # scoped pins ---------------------------------------------------------------
    if want("family_pin"):
        family = fam[0]
        inside = [j for j in everywhere if fam[j] == family]
        t = _pick(controls, "experience", must_be_on=inside, prefer_on=everywhere)
        if t is None:
            skip("family_pin", "no experience on every in-family control page")
        else:
            src = f"family_pin: never include {label(t)} for {family} roles"
            out.append(Scenario(
                "family_pin", "scope", f"hard suppress scoped to the {family} role family",
                [hard_pref(f"For {family} roles, never include {label(t)}.", label(t),
                           scope="role_family", scope_value=family)],
                lambda j, t=t, src=src, inside=inside: (
                    {"absent_items": [{**t, "source": src}]} if j in inside else None)))
    if want("job_pin"):
        t = _pick(controls, "project", must_be_on=[0], prefer_on=everywhere) \
            or _pick(controls, "experience", must_be_on=[0], prefer_on=everywhere)
        if t is None:
            skip("job_pin", "no item on the first control page")
        else:
            src = f"job_pin: never include {label(t)} on job 0"
            out.append(Scenario(
                "job_pin", "scope", "hard suppress scoped to the first job",
                [hard_pref(f"On this job, never include {label(t)}.", label(t),
                           scope="job", scope_job=0)],
                lambda j, t=t, src=src: (
                    {"absent_items": [{**t, "source": src}]} if j == 0 else None)))

    # soft ----------------------------------------------------------------------
    if want("soft"):
        counts = _present_counts(controls, "project")
        all_projects = [(p["name"],) for p in fixture.projects]
        absent = sorted(((len(counts.get(ident, [])), ident) for ident in all_projects))
        emph = _item_target("project", absent[0][1]) if absent and absent[0][0] < n else None
        sup = _pick(controls, "experience", must_be_on=[], prefer_on=everywhere)
        if emph is None and sup is None:
            skip("soft", "nothing to emphasize or to suppress")
        else:
            steps, want_in, want_out = [], [], []
            if emph is not None:
                steps.append(hard_pref(f"I would like {label(emph)} on my resume.", label(emph),
                                       polarity="emphasize", strength=3))
                want_in.append({**emph, "source": f"soft: emphasize {label(emph)}"})
            if sup is not None:
                steps.append(hard_pref(f"I would rather not show {label(sup)}.", label(sup),
                                       polarity="suppress", strength=3))
                want_out.append({**sup, "source": f"soft: suppress {label(sup)}"})
            out.append(Scenario(
                "soft", "soft", "strength-3 emphasize and suppress: reported, never a gate",
                steps, lambda j: None,
                soft=lambda j, i=want_in, o=want_out: {"required_items": i, "absent_items": o}))

    # page rules ----------------------------------------------------------------
    if want("rule_pages"):
        out.append(Scenario(
            "rule_pages", "rule", "hard: one page",
            [rule("Keep it to one page.", "limit", {"pages": 1})],
            lambda j: {"max_pages": {"value": 1, "source": "rule_pages: keep it to one page"}},
            needs_pages=True))
    if want("rule_exclude"):
        out.append(Scenario(
            "rule_exclude", "rule", "hard: no projects section",
            [rule("No projects section on my resume.", "limit", {"projects": 0})],
            lambda j: {"absent_sections": [
                {"name": "projects", "source": "rule_exclude: no projects section"}]}))
    if want("rule_order"):
        order = ["skills", "projects", "experience"]
        out.append(Scenario(
            "rule_order", "rule", "hard: skills, then projects, then experience",
            [rule("Put skills first, then projects, then experience.", "section_order",
                  {"order": order})],
            lambda j, order=order: {"section_order": {
                "order": order, "source": "rule_order: skills, projects, experience"}}))
    if want("rule_bullets"):
        out.append(Scenario(
            "rule_bullets", "rule", "hard: two bullets an item",
            [rule("At most two bullets under any role or project.", "limit",
                  {"bullets_per_item": 2})],
            lambda j: {"max_bullets_per_item": {
                "value": 2, "source": "rule_bullets: at most two bullets an item"}}))
    if want("rule_skills"):
        out.append(Scenario(
            "rule_skills", "rule", "hard: six skills",
            [rule("List at most six skills.", "limit", {"max_skills": 6})],
            lambda j: {"max_skills": {"value": 6, "source": "rule_skills: at most six skills"}}))
    if want("rule_narrower"):
        family = fam[0]
        out.append(Scenario(
            "rule_narrower", "rule",
            "a hard global two bullets an item; a role-family rule for three is refused",
            [rule("At most two bullets under any role or project.", "limit",
                  {"bullets_per_item": 2}),
             rule(f"For {family} roles, three bullets an item is fine.", "limit",
                  {"bullets_per_item": 3}, strength=5, scope="role_family", scope_value=family,
                  expect_refused=True)],
            lambda j: {"max_bullets_per_item": {
                "value": 2, "source": "rule_narrower: at most two bullets an item (global, hard)"}}))

    # durability ----------------------------------------------------------------
    t_forbid = item_target_for("project") or item_target_for("experience")
    if want("supersede_forbid"):
        if t_forbid is None:
            skip("supersede_forbid", "no item on a control page")
        else:
            t = t_forbid
            out.append(Scenario(
                "supersede_forbid", "durability",
                "a soft emphasize, then superseded by a hard suppress of the same item",
                [hard_pref(f"I like {label(t)} on my resume.", label(t), polarity="emphasize",
                           strength=3)],
                lambda j: None,
                soft=lambda j, t=t: {"required_items": [
                    {**t, "source": f"supersede_forbid: I like {label(t)}"}]},
                stage2={"steps": [hard_pref(f"Never include {label(t)}.", label(t))],
                        "expect": lambda j, t=t: {"absent_items": [
                            {**t, "source": f"supersede_forbid: never include {label(t)}"}]}}))
    if want("supersede_allow"):
        t = item_target_for("project")
        if t is None:
            skip("supersede_allow", "no project on a control page")
        else:
            out.append(Scenario(
                "supersede_allow", "durability",
                "a hard suppress, then superseded by a hard emphasize of the same project",
                [hard_pref(f"Never include {label(t)}.", label(t))],
                lambda j, t=t: {"absent_items": [
                    {**t, "source": f"supersede_allow: never include {label(t)}"}]},
                stage2={"steps": [hard_pref(f"Always include {label(t)}.", label(t),
                                            polarity="emphasize")],
                        "expect": lambda j, t=t: {"required_items": [
                            {**t, "source": f"supersede_allow: always include {label(t)}"}]}}))
    return out, skipped


# ── running ──────────────────────────────────────────────────────────────────

def _store(uid, scenario_steps: List[Dict], job_ids: List[str]) -> List[Dict]:
    """Record a set through the real tools; returns what each call answered."""
    from harness.contract import invoke
    answers = []
    for step in scenario_steps:
        args = dict(step["args"])
        if step.get("scope_job") is not None:
            args["scope_value"] = job_ids[step["scope_job"]]
        out = invoke(step["tool"], uid, args)
        answers.append({"tool": step["tool"], "status": out.get("status"),
                        "reason": out.get("reason"), "expect_refused": step.get("expect_refused"),
                        "anchor": (out.get("anchor") or {}).get("status")})
    return answers


COMPILE = True          # compile the PDF for the page-count check when an engine is installed


def _render(uid, job_id: str, want_pages: bool) -> Tuple[Optional[str], Optional[int]]:
    fmt = "pdf" if (want_pages and COMPILE and has_latex_engine()) else "tex"
    out = _call("render", uid, {"job_id": job_id, "format": fmt})
    pages = out.get("pages")
    return Path(out["tex_path"]).read_text(encoding="utf-8"), pages


def _head_committed(uid, job_id: str) -> bool:
    return bool(_call("get_head", uid, {"job_id": job_id})["head"])


_ID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\b")


def _plain_ids(text: Any) -> str:
    """A message without the random ids it quotes (a rule's first eight hex digits, a UUID), so
    two runs write the same report."""
    return _ID.sub("<id>", str(text))


def run_job(uid, job_id: str, task: Dict, host: str, tempt: Sequence[str] = (),
            want_pages: bool = False) -> Dict:
    """One plan on one job. Returns the facts the metrics need, with the page's `.tex`."""
    if host == "naive":
        program, block = naive_program(uid, job_id, task, tempt), None
    else:
        program, block = obedient_program(uid, job_id, task)
    result = _call("execute_plan", uid, {"program": program})
    committed = bool(result.get("committed"))
    no_change = str(result.get("reason") or "").startswith("no_change")
    settled = committed or no_change
    tex = pages = None
    if settled and _head_committed(uid, job_id):
        tex, pages = _render(uid, job_id, want_pages)
    nodes = result.get("nodes") or []
    return {
        "committed": committed, "no_change": no_change,
        "tex": tex, "pages": pages,
        "violations": [{"check": v.get("check"), "detail": _plain_ids(v.get("detail"))[:160]}
                       for v in result.get("violations") or []],
        "refused": [_plain_ids(n["reason"]) for n in nodes if n["status"] == "refused"],
        "pref_refusals": [_plain_ids(n["reason"]) for n in nodes
                          if n["status"] == "refused" and (n["reason"] or "").startswith(PREF_REASONS)],
        "reverted": sum(1 for n in nodes if n["status"] == "reverted"),
        "not_accepted": [_plain_ids(f"{n['id']}: {n['status']} ({(n.get('reason') or '')[:110]})")
                         for n in nodes if n["status"] in ("refused", "reverted")],
        "soft": [s for s in result.get("soft_preferences") or []],
        "block": block,
    }


def _units(inv: Dict) -> Dict[str, List]:
    """What a page holds, as comparable units."""
    out: Dict[str, List] = {"item": [], "bullet": [], "skill": [], "section": []}
    for it in inv["experiences"]:
        key = ("experience", it["title"].casefold(), it["company"].casefold())
        out["item"].append(key)
        out["bullet"] += [(key, b.casefold()) for b in it["bullets"]]
    for it in inv["projects"]:
        key = ("project", it["name"].casefold())
        out["item"].append(key)
        out["bullet"] += [(key, b.casefold()) for b in it["bullets"]]
    out["skill"] = [s.casefold() for s in inv["skills"]]
    out["section"] = list(inv["sections"])
    return out


def _unit_count(u: Dict[str, List]) -> int:
    return sum(len(v) for v in u.values())


def _item_key(w: Dict) -> Tuple:
    return (("experience", w["title"].casefold(), w["company"].casefold())
            if w["kind"] == "experience" else ("project", w["name"].casefold()))


def justified_drops(control: Dict, page: Dict, expect: Optional[Dict], soft: Optional[Dict],
                    retailor: bool = False) -> Tuple[int, List[str], int]:
    """How many units the control page held, which of those the page lost that no in-scope
    constraint asked for, and how many projects it lost because the only way to put a project on
    a page is `replace`, which takes another off: to make room for one the user asked to keep, or
    (`retailor`, a plan on a version an earlier plan already cut) because a project that version
    dropped cannot be brought back without taking another off. `expect` is the hard expectation on this job, `soft` the soft
    preferences' (an emphasized item, a suppressed one).

    A loss is justified by: a suppressed item (hard or soft) and its bullets; a section the
    rules leave off and everything in it; a bullet that says a banned term and the skill that is
    one; bullets above a per-item limit; skills above a skills cap; and the slot a required item
    takes from a project. The scripted host also tightens an experience to three bullets (its
    own policy; the acceptance rule reverts it in some contexts and not others), so bullets
    above three an experience held are never counted as lost."""
    c, p = _units(control), _units(page)
    expect, soft = expect or {}, soft or {}
    gone = {_item_key(w) for w in (expect.get("absent_items") or []) + (soft.get("absent_items") or [])}
    banned = list(expect.get("banned_terms") or [])
    off = {s["name"] for s in expect.get("absent_sections") or []}
    cap_rule = (expect.get("max_bullets_per_item") or {}).get("value")
    cap_skills = (expect.get("max_skills") or {}).get("value")
    required = (expect.get("required_items") or []) + (soft.get("required_items") or [])
    displaced = sum(1 for w in required if w["kind"] == "project")
    swapped = 0

    def section_of(key: Tuple) -> str:
        return "experience" if key[0] == "experience" else "projects"

    held: Dict[Tuple, List[str]] = {}
    for key, b in c["bullet"]:
        held.setdefault(key, []).append(b)

    def says(b: str) -> bool:
        return any(checker.mentions(b, t["term"], t.get("aliases") or ()) for t in banned)
    # An item every one of whose bullets says a banned term cannot be shown without it.
    unshowable = {key for key, bullets in held.items() if bullets and all(says(b) for b in bullets)}

    lost: List[str] = []
    on_page = set(p["item"])
    for it in c["item"]:
        if it in on_page or it in gone or it in unshowable or section_of(it) in off:
            continue
        if it[0] == "project" and (retailor or displaced > 0):
            displaced = max(0, displaced - 1)
            swapped += 1
            continue
        lost.append(f"item {it[1:]}")

    here: Dict[Tuple, set] = {}
    for key, b in p["bullet"]:
        here.setdefault(key, set()).add(b)
    for key, bullets in held.items():
        if key in gone or section_of(key) in off or key not in on_page:
            continue                                   # its item is accounted for above
        missing = [b for b in bullets if b not in here.get(key, set())]
        said = [b for b in missing if any(checker.mentions(b, t["term"], t.get("aliases") or ())
                                          for t in banned)]
        rest = [b for b in missing if b not in said]
        caps = [x for x in (cap_rule, 3 if key[0] == "experience" else None) if x]
        cap = min(caps) if caps else None
        allowance = max(0, len(bullets) - cap) if cap else 0
        # bullets the host swapped in (on the page, not in the control) fill the vacated slots
        lost += [f"bullet {b[:40]!r}" for b in rest[allowance:]]

    page_skills = set(p["skill"])
    allowance = max(0, len(c["skill"]) - cap_skills) if cap_skills else 0
    for sk in c["skill"]:
        if sk in page_skills or "skills" in off:
            continue
        if any(checker.mentions(sk, t["term"], t.get("aliases") or ()) for t in banned):
            continue
        if allowance > 0:
            allowance -= 1
            continue
        lost.append(f"skill {sk}")
    return _unit_count(c), lost, swapped


def _check(run: Dict, expect: Optional[Dict]) -> Optional[Dict]:
    if run.get("tex") is None or not expect:
        return None
    return checker.check(run["tex"], expect, run.get("pages"))


def run_profile(profile: str, jobs: List[Dict], only: Optional[Sequence[str]], log=None) -> Dict:
    """Every seeded set, on both hosts, over every job, for one profile."""
    from eval.profile_fixture import load_profile

    fixture = load_profile(PROFILES_DIR / f"{profile}.md")
    say = log or (lambda *_: None)
    tag = re.sub(r"[^a-z0-9]+", "", profile.lower())[:24]
    n = len(jobs)

    # controls: the same hosts on a store with no preference or rule
    def control(tempt: Tuple[str, ...]) -> List[Optional[Dict]]:
        uid = _seed(fixture, f"{tag}.control.{'-'.join(tempt) or 'plain'}")
        ids = [open_job(uid, j["task"]) for j in jobs]
        outs: List[Optional[Dict]] = []
        for j, task in enumerate(jobs):
            run = run_job(uid, ids[j], task["task"], "naive", tempt)
            if run["tex"] is None:
                outs.append(None)
                continue
            outs.append({"inv": checker.inventory(run["tex"]), "tex": run["tex"],
                         "committed": run["committed"]})
        return outs

    base_controls = control(())
    scenarios, skipped = build_scenarios(fixture, base_controls, jobs, only)
    controls_by_tempt: Dict[Tuple[str, ...], List[Optional[Dict]]] = {(): base_controls}
    results: List[Dict] = []
    setup_findings: List[Dict] = []

    for sc in scenarios:
        if sc.tempt not in controls_by_tempt:
            controls_by_tempt[sc.tempt] = control(sc.tempt)
        ctl = controls_by_tempt[sc.tempt]
        for host in ("naive", "obedient"):
            say(f"  {profile} / {sc.name} / {host}")
            uid = _seed(fixture, f"{tag}.{sc.name}.{host}")
            ids = [open_job(uid, j["task"]) for j in jobs]
            for ans in _store(uid, sc.steps, ids):
                bad = (ans["status"] != "refused") if ans["expect_refused"] else (
                    ans["status"] not in ("stored", "superseded", "already_recorded"))
                if bad:
                    setup_findings.append({"profile": profile, "set": sc.name, **ans})
            stage1: List[Dict] = []
            for j, job in enumerate(jobs):
                run = run_job(uid, ids[j], job["task"], host, sc.tempt, sc.needs_pages)
                stage1.append(run)
                ref = ctl[j] if host == "naive" else base_controls[j]
                results.append(_record(profile, sc, host, 1, j, job, run, ref,
                                       sc.expect(j), sc.soft(j)))
            if sc.stage2:
                for ans in _store(uid, sc.stage2["steps"], ids):
                    if ans["status"] not in ("stored", "superseded", "already_recorded"):
                        setup_findings.append({"profile": profile, "set": sc.name,
                                               "stage": 2, **ans})
                for j, job in enumerate(jobs):
                    run = run_job(uid, ids[j], job["task"], host, sc.tempt)
                    ref = ctl[j] if host == "naive" else base_controls[j]
                    results.append(_record(profile, sc, host, 2, j, job, run, ref,
                                           sc.stage2["expect"](j), None))
    return {"profile": profile, "runs": results, "skipped": skipped,
            "setup_findings": setup_findings, "scenarios": [s.name for s in scenarios]}


def _record(profile, sc: Scenario, host: str, stage: int, j: int, job: Dict, run: Dict,
            control: Optional[Dict], expect: Optional[Dict], soft: Optional[Dict]) -> Dict:
    """One run as the facts the metrics read: no page text, no ids."""
    checked = _check(run, expect)
    armed = bool(control and expect and checker.check(control["tex"], expect,
                                                      None)["violations"])
    in_scope = expect is not None
    violating = bool(checked and checked["violations"])
    if run["committed"] and run["tex"] is not None:
        outcome = "violation" if violating else "held"
    elif run["no_change"] and run["tex"] is not None:
        outcome = "stale" if violating else "unchanged"
    elif run["violations"]:
        outcome = "refused"
    else:
        outcome = "not_committed"
    units = dropped = None
    swapped = 0
    if control and run["tex"] is not None and (run["committed"] or run["no_change"]):
        units, dropped, swapped = justified_drops(control["inv"], checker.inventory(run["tex"]),
                                                  expect, soft, retailor=stage == 2)
    soft_asked = soft_applied = 0
    if soft and run["tex"] is not None:
        inv = checker.inventory(run["tex"])
        page = {"experience": {(it["title"].casefold(), it["company"].casefold())
                               for it in inv["experiences"]},
                "project": {it["name"].casefold() for it in inv["projects"]}}

        def present(w: Dict) -> bool:
            if w["kind"] == "experience":
                return (w["title"].casefold(), w["company"].casefold()) in page["experience"]
            return w["name"].casefold() in page["project"]
        for w in soft.get("required_items") or []:
            soft_asked += 1
            soft_applied += present(w)
        for w in soft.get("absent_items") or []:
            soft_asked += 1
            soft_applied += not present(w)
    return {
        "profile": profile, "set": sc.name, "group": sc.group, "host": host, "stage": stage,
        "job": j, "family": job["family"], "in_scope": in_scope, "armed": armed,
        "outcome": outcome, "committed": run["committed"], "no_change": run["no_change"],
        "violations": (checked or {}).get("violations") or [],
        "gate_violations": run["violations"], "skipped_checks": (checked or {}).get("skipped") or [],
        "not_accepted": run["not_accepted"],
        "pref_refusals": run["pref_refusals"], "refused": len(run["refused"]),
        "units": units, "unjustified_drops": dropped, "swapped_for_required": swapped,
        "control_committed": bool(control and control["committed"]),
        "soft_asked": soft_asked, "soft_applied": soft_applied,
    }


# ── metrics ──────────────────────────────────────────────────────────────────

def summarize(runs: List[Dict]) -> Dict:
    """The metrics from every run's record. Everything is a count over records, so two runs over
    the same store give the same numbers."""
    naive = [r for r in runs if r["host"] == "naive" and r["stage"] == 1 and r["in_scope"]]
    traps = [r for r in naive if r["armed"]]
    naive_all = [r for r in runs if r["host"] == "naive" and r["in_scope"]]
    viol_naive = [r for r in naive_all if r["outcome"] == "violation"]
    obedient = [r for r in runs if r["host"] == "obedient" and r["stage"] == 1]
    ob_in = [r for r in obedient if r["in_scope"]]
    ob_viol = [r for r in runs if r["host"] == "obedient" and r["in_scope"]
               and r["outcome"] == "violation"]

    def leaks(host: str) -> Dict:
        pool = [r for r in runs if r["host"] == host and r["stage"] == 1
                and not r["in_scope"] and r["group"] == "scope"]
        bad = [r for r in pool if (r["control_committed"] and not r["committed"]
                                   and not r["no_change"]) or r["unjustified_drops"]]
        return {"checked": len(pool), "leaks": len(bad),
                "cases": [{k: r[k] for k in ("profile", "set", "job", "family", "outcome")}
                          for r in bad]}

    # Over-block reads every obedient run, the re-tailors after a supersede included.
    ob_all = [r for r in runs if r["host"] == "obedient"]
    ob_pages = [r for r in ob_all if r["units"] is not None]
    units = sum(r["units"] for r in ob_pages)
    drops = sum(len(r["unjustified_drops"] or []) for r in ob_pages)
    swapped = sum(r["swapped_for_required"] for r in ob_pages)
    uncommitted = [r for r in ob_all if r["control_committed"] and not r["committed"]
                   and not r["no_change"]]
    refusals = sum(len(r["pref_refusals"]) for r in ob_all)
    soft = [r for r in obedient if r["soft_asked"]]
    stage2 = [r for r in runs if r["stage"] == 2]

    def durable(r: Dict, host: str) -> bool:
        if host == "obedient":
            return r["outcome"] in ("held", "unchanged")
        return r["outcome"] in ("held", "refused", "unchanged")
    dur = {h: {"checked": len([r for r in stage2 if r["host"] == h]),
               "passed": len([r for r in stage2 if r["host"] == h and durable(r, h)])}
           for h in ("naive", "obedient")}
    for h in dur:
        dur[h]["rate"] = round(dur[h]["passed"] / dur[h]["checked"], 4) if dur[h]["checked"] else None

    def rate(a: int, b: int) -> Optional[float]:
        return round(a / b, 4) if b else None

    by_set: Dict[str, Dict] = {}
    for r in runs:
        s = by_set.setdefault(r["set"], {"group": r["group"], "naive": {}, "obedient": {}})
        d = s[r["host"]]
        d["runs"] = d.get("runs", 0) + 1
        d["armed"] = d.get("armed", 0) + int(bool(r["armed"] and r["in_scope"]))
        d[r["outcome"]] = d.get(r[ "outcome"], 0) + 1
    naive_findings = [{k: r[k] for k in ("profile", "set", "job", "family", "stage")}
                      | {"violations": r["violations"]} for r in viol_naive]
    return {
        "naive": {
            "runs_in_scope": len(naive_all), "traps_armed": len(traps),
            "held": len([r for r in traps if r["outcome"] == "held"]),
            "refused": len([r for r in traps if r["outcome"] == "refused"]),
            "unchanged": len([r for r in traps if r["outcome"] == "unchanged"]),
            "hard_violations": len(viol_naive),
            "hard_violation_rate_of_traps": rate(len([r for r in traps if r["outcome"] == "violation"]),
                                                 len(traps)),
            "hard_violation_rate_of_runs": rate(len(viol_naive), len(naive_all)),
            "findings": naive_findings,
            "scope_leak": leaks("naive"),
        },
        "obedient": {
            "runs": len(obedient), "runs_in_scope": len(ob_in),
            "hard_violations": len(ob_viol),
            "hard_violation_rate": rate(len(ob_viol), len([r for r in runs if r["host"] == "obedient"
                                                         and r["in_scope"]])),
            "over_block": {"control_units": units, "unjustified_drops": drops,
                           "rate": rate(drops, units), "pref_refusals": refusals,
                           "swapped_for_required": swapped,
                           "uncommitted_runs": len(uncommitted),
                           "cases": [f"{r['profile']}/{r['set']}/job{r['job']}: {d}"
                                     for r in ob_pages for d in (r["unjustified_drops"] or [])][:40]},
            "scope_leak": leaks("obedient"),
            "soft": {"asked": sum(r["soft_asked"] for r in soft),
                     "applied": sum(r["soft_applied"] for r in soft),
                     "rate": rate(sum(r["soft_applied"] for r in soft),
                                  sum(r["soft_asked"] for r in soft))},
        },
        "durability": dur,
        "by_set": by_set,
    }


# ── the run ──────────────────────────────────────────────────────────────────

def run_benchmark(profiles: Sequence[str], jobs: int, only: Optional[Sequence[str]] = None,
                  log=None, compile_pages: bool = True) -> Dict:
    """The whole benchmark against the store the caller has set up. Returns the report dict.
    `compile_pages` False never starts a LaTeX engine: the page rule is then checked from an
    estimate and the report says so."""
    global COMPILE
    from eval.tailoring_benchmark import load_tasks
    COMPILE = compile_pages
    _RUN[0] += 1

    tasks = load_tasks(limit=jobs)
    job_meta = [{"task": t, "family": FAMILY.get(t.get("role_family") or "", "other"),
                 "label": t.get("role_family")} for t in tasks]
    with _Env():
        per_profile = [run_profile(p, job_meta, only, log) for p in profiles]
    runs = [r for p in per_profile for r in p["runs"]]
    return {
        "benchmark": "preference_compliance", "issue": 299,
        "profiles": list(profiles),
        "jobs": [{"index": i, "role_family": m["label"], "harness_family": m["family"]}
                 for i, m in enumerate(job_meta)],
        "latex_engine": has_latex_engine(),
        "page_check": ("estimated from bullet length (no LaTeX engine used)"
                       if not (has_latex_engine() and compile_pages) or any(
                           r["skipped_checks"] for r in runs)
                       else "compiled page count (rule_pages only)"),
        "sets": list(SET_NAMES), "metrics": summarize(runs),
        "skipped_sets": [dict(s, profile=p["profile"]) for p in per_profile for s in p["skipped"]],
        "setup_findings": [f for p in per_profile for f in p["setup_findings"]],
        "runs": runs,
    }


def render_markdown(report: Dict, date: str) -> str:
    m = report["metrics"]
    nv, ob = m["naive"], m["obedient"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.2f}%"  # noqa: E731
    lines = [
        f"# Preference compliance, {date}",
        "",
        f"{len(report['profiles'])} profiles x {len(report['jobs'])} jobs x "
        f"{len(report['sets'])} seeded sets, each on a naive and an obedient host "
        f"({len(report['runs'])} runs). Page count: {report['page_check']}.",
        "",
        "| Metric | Result | Target |",
        "|---|---|---|",
        f"| Naive host: hard violations rendered | {nv['hard_violations']} of "
        f"{nv['runs_in_scope']} in-scope runs ({nv['traps_armed']} armed traps) | 0 |",
        f"| Naive host: scope leak | {nv['scope_leak']['leaks']} of "
        f"{nv['scope_leak']['checked']} out-of-scope runs | 0 |",
        f"| Obedient host: hard violations rendered | {ob['hard_violations']} | 0 |",
        f"| Obedient host: scope leak | {ob['scope_leak']['leaks']} of "
        f"{ob['scope_leak']['checked']} | 0 |",
        f"| Obedient host: over-block rate | {pct(ob['over_block']['rate'])} "
        f"({ob['over_block']['unjustified_drops']} of {ob['over_block']['control_units']} units) "
        f"| <= 2% |",
        f"| Obedient host: projects swapped out to make room for one the user asked to keep "
        f"(not counted above: `replace` is the only way to add one) | "
        f"{ob['over_block']['swapped_for_required']} | report |",
        f"| Obedient host: refusals for a preference it was told about | "
        f"{ob['over_block']['pref_refusals']} | 0 |",
        f"| Obedient host: runs that did not commit although the control did | "
        f"{ob['over_block']['uncommitted_runs']} | 0 |",
        f"| Durability (obedient, after a supersede) | {m['durability']['obedient']['passed']} of "
        f"{m['durability']['obedient']['checked']} | all |",
        f"| Durability (naive: stopped or compliant) | {m['durability']['naive']['passed']} of "
        f"{m['durability']['naive']['checked']} | all |",
        f"| Soft preferences applied (obedient, reported) | {ob['soft']['applied']} of "
        f"{ob['soft']['asked']} | report |",
        "",
        "## By set",
        "",
        "| Set | Group | Naive outcomes | Obedient outcomes |",
        "|---|---|---|---|",
    ]
    for name in report["sets"]:
        s = m["by_set"].get(name)
        if not s:
            continue
        fmt = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items())  # noqa: E731
                                  if k not in ("runs", "armed")) or "-"
        lines.append(f"| {name} | {s['group']} | {fmt(s['naive'])} (armed {s['naive'].get('armed', 0)}) "
                     f"| {fmt(s['obedient'])} |")
    if nv["findings"]:
        lines += ["", "## Enforcement gaps (naive host rendered a violation)", ""]
        for f in nv["findings"][:20]:
            lines.append(f"- {f['profile']} / {f['set']} / job {f['job']} ({f['family']}), "
                         f"stage {f['stage']}: " + "; ".join(v["detail"] for v in f["violations"]))
    if report["setup_findings"]:
        lines += ["", "## Setup answers that were not what the set expected", ""]
        for f in report["setup_findings"][:20]:
            lines.append(f"- {f['profile']} / {f['set']}: {f['tool']} -> {f['status']} "
                         f"({f.get('reason')})")
    if report["skipped_sets"]:
        lines += ["", "## Sets not built", ""]
        for s in report["skipped_sets"]:
            lines.append(f"- {s['profile']} / {s['set']}: {s['reason']}")
    lines.append("")
    return "\n".join(lines)


def _setup_store(workdir: Path) -> None:
    """A throwaway SQLite store this run owns: `DATABASE_URL` is overwritten, never trusted."""
    os.environ["DATABASE_URL"] = f"sqlite:///{workdir / 'preference_compliance.db'}"
    os.environ["ART_DATA_DIR"] = str(workdir / "data")
    os.environ["ART_JEV_MODE"] = "off"
    import config
    config.APP_DATA_DIR = workdir / "data"
    (workdir / "data").mkdir(parents=True, exist_ok=True)
    from database.db import init_db
    init_db()


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--profiles", type=int, default=5, help="how many of the five profiles")
    p.add_argument("--jobs", type=int, default=6, help="how many postings (round-robin by family)")
    p.add_argument("--sets", default=None, help="comma-separated subset of: " + ", ".join(SET_NAMES))
    p.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    p.add_argument("--date", default=None, help="date in the file names (default: today)")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--no-compile", action="store_true",
                   help="never start a LaTeX engine; check the page rule from an estimate")
    args = p.parse_args(argv)
    only = [s.strip() for s in args.sets.split(",")] if args.sets else None
    if only and set(only) - set(SET_NAMES):
        raise SystemExit(f"unknown set(s): {', '.join(sorted(set(only) - set(SET_NAMES)))}")
    profiles = list(PROFILES[:max(1, min(args.profiles, len(PROFILES)))])
    date = args.date or datetime.date.today().isoformat()

    import logging
    logging.getLogger().setLevel(logging.WARNING)
    for noisy in ("knowledge_graph.builder", "harness", "agents", "services"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        _setup_store(Path(tmp))
        report = run_benchmark(profiles, args.jobs, only, log=lambda m: print(m, file=sys.stderr),
                               compile_pages=not args.no_compile)
        import database.db as _db
        _db.engine.dispose()
    text = render_markdown(report, date)
    print(text)
    if not args.no_write:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        # One run per line, so a diff of two reports reads as the runs that changed.
        head = {k: v for k, v in report.items() if k != "runs"}
        body = ",\n".join("  " + json.dumps(r, sort_keys=True) for r in report["runs"])
        (args.out_dir / f"preference_compliance_{date}.json").write_text(
            json.dumps(head, indent=1, sort_keys=True)[:-2] + ',\n "runs": [\n' + body + "\n ]\n}\n",
            encoding="utf-8")
        (args.out_dir / f"preference_compliance_{date}.md").write_text(text, encoding="utf-8")
    nv, ob = report["metrics"]["naive"], report["metrics"]["obedient"]
    bad = (nv["hard_violations"] or nv["scope_leak"]["leaks"] or ob["hard_violations"]
           or ob["scope_leak"]["leaks"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
