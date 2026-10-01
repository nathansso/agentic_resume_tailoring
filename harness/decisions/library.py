"""The bullet library's two judgment calls on Jev (#199): which approved variant fits a job, and
which saved track a job starts from.

Both are one `choice` over an enumerated set with a null catch-all, asked through
`engine.decide`, so both are cached, replayable and fall back to #229's rules with no key, mode
`off` or an API error (`harness/library.py` keeps those rules and runs them unchanged).

**Variant choice, `variant_choice@v1`.** One request per (job, item) that has at least one
approved variant. The state is narrow, because Jev is distracted by large state:

    {job: {title, requirements: [top 8 required/preferred sentences]}, item: {title}}

The question is one `choice`: "Which approved bullet best fits this job?". Its options are the
item's approved variants, keyed by variant id with the bullet text as the description, plus
`no_match` ("none of these fits the job"). **The gate is the probability Jev puts on any
variant**, one minus its `no_match`: when that is at least `TAU_VARIANT` the result is its likeliest
variant, else `no_match`. (The first design gated on the argmax's own probability; two good phrasings
of one bullet split Jev's mass between them, so neither reached the threshold while `no_match` stayed
low, and the close calls were lost. `eval/library_labels/REPORT.md` measures both.) `propensity` is
the whole distribution (every variant and `no_match`). A bullet that only shares the posting's keywords
but describes another kind of role is named in the question as not fitting.

**Track baseline, `track_baseline@v1`.** One request per job, only when the user has saved at
least one baseline. The state is `{title, requirements}`. The options are the saved tracks, each
described in code as the track's name in words plus the title of the job its baseline came from
("A saved resume track for data science roles, started from a job titled 'Data Scientist'"), plus
`none`. The result is Jev's own choice when it is a track and its probability is at least
`TAU_BASELINE`, else none (the argmax rule: a track is one start, so there is no close call to pool). A host that states `metadata.role_family` and a track with exactly that
name exists wins without asking (`harness/library.choose_baseline`): the host's explicit
statement is not a judgment call.

**Requirements.** The job's required and preferred requirements, most critical first and then in
posting order, at most `MAX_REQUIREMENTS` of them, each cut to `REQUIREMENT_MAX_CHARS`
(`top_requirements`). Incidental ones are left out, as in `coverage.py`.

Thresholds are fitted on `eval/library_labels/` (see the comment on the constants) and never
taken from Jev's reported confidence. Model-free apart from `engine.decide`, imported only when a
decision is asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from harness.decisions.questions import Answer, Choice

VARIANT_POINT = "variant_choice"
VARIANT_VERSION = "variant_choice@v1"
BASELINE_POINT = "track_baseline"
BASELINE_VERSION = "track_baseline@v1"

NO_MATCH = "no_match"           # the variant question's catch-all
NONE = "none"                   # the track question's catch-all
MAX_REQUIREMENTS = 8            # Jev is distracted by large state: the posting's top requirements only
REQUIREMENT_MAX_CHARS = 300
MAX_TRACKS = 254                # a choice takes at most 255 options, one of them `none`
MAX_VARIANTS = 254
TAU_FLOOR = 0.5                 # a pick needs Jev to call it more likely than not
_ROUND = 4

# Fitted (#199) on eval/library_labels/ (48 variant cases, 36 baseline cases, synthetic, labels
# planner-reviewed and pending the user's spot-check), against jev-1.13.0's recorded answers to
# variant_choice@v1 and track_baseline@v1. Refit with `python eval/fit_library_thresholds.py analyze`
# whenever the model or a question changes. What each threshold gates: TAU_VARIANT, the probability Jev puts on
# ANY variant (one minus its no_match; the pick is its likeliest variant); TAU_BASELINE, the probability of
# Jev's own choice when that is a track. The rules, in priority order, over grid values of at least TAU_FLOOR,
# each then taking the value closest to the middle of its gap, ties to the higher (#126): TAU_BASELINE, no
# wrong baseline (a track outside the label's acceptable set, or any track for a job no track fits), then the
# most correct picks; TAU_VARIANT, no pick of a variant labelled poor_fit, then the most correct picks.
# **Both are fitted as if the code rules did not exist** (#202): the host's explicit role_family winning
# without a question, the drift guard and the fallbacks are defence in depth and never grounds for a looser
# threshold.
#   TAU_VARIANT 0.50: gated on the mass off no_match, because two good phrasings of one bullet split Jev's
#     probability between them (close_call: 8/8 right on Jev's own choice, 4/8 when its own probability had
#     to reach 0.50). Jev's mass reaches 0.50 on a poor-fit variant in 0 of 48 cases. The highest poor-fit
#     mass is c_esri_halden, 0.46 (a data science job against teaching bullets), and the lowest right pick
#     a_mg_dashboard, 0.51: the floor sits inside a 0.05 gap, 0.04 over the worst poor-fit case and 0.01 under
#     the lowest right one, so the first key never binds and the second takes the lowest grid value: 32 right
#     picks at 0.50, 30 at 0.55, 29 at 0.60. At 0.50 Jev picks 33 variants, 32 right and one
#     (d_tebra_backfill, n_dbt) not the label's but not poor-fit either, and leaves no case it should pick.
#     The gap is thin on both sides: a poor-fit case 0.04 under the threshold is one rewording from a pick.
#   TAU_BASELINE 0.50: Jev picked a wrong track in 0 of 36 cases at any grid value, so 0.50 to 0.65 give the
#     same 21 right picks and the middle of the gap (0 to the lowest right pick, 0.66) is the floor. A set with
#     no wrong pick cannot pull the threshold under the floor, and it does not raise it either: both are the
#     floor because nothing here separates a wrong pick from a right one. A track is one start, so there is no
#     close call to pool and its own probability stays the gate. The thin part is the sets, not the rule:
#     refit when the model or a question changes, and with adversarial cases (a track that is almost right, a
#     job the host mislabels) once real use turns some up.
TAU_VARIANT = 0.50
TAU_BASELINE = 0.50


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), _ROUND)


# ── the state ────────────────────────────────────────────────────────────────

def top_requirements(requirements: Optional[Iterable[Dict]],
                     limit: int = MAX_REQUIREMENTS) -> List[str]:
    """The required and preferred requirement sentences, most critical first, then in posting
    order, at most `limit`, each cut to `REQUIREMENT_MAX_CHARS`. Incidental ones are left out."""
    from harness.decisions.coverage import eligible_requirements

    rows = eligible_requirements(requirements or [])
    ranked = sorted(enumerate(rows), key=lambda ir: (-ir[1]["criticality"], ir[0]))
    out: List[str] = []
    for _, row in ranked:
        text = _norm(row["text"])[:REQUIREMENT_MAX_CHARS].rstrip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def job_state(title: str, requirements: Optional[Iterable[Dict]]) -> Dict[str, Any]:
    return {"title": _norm(title), "requirements": top_requirements(requirements)}


def variant_state(title: str, requirements: Optional[Iterable[Dict]], item_title: str) -> Dict[str, Any]:
    """The whole state of a variant question: the job's title and top requirements, and the item's
    title (a role at a company, or a project's name). Nothing else from the resume or the posting."""
    return {"job": job_state(title, requirements), "item": {"title": _norm(item_title)}}


def baseline_state(title: str, requirements: Optional[Iterable[Dict]]) -> Dict[str, Any]:
    return job_state(title, requirements)


# ── the questions ────────────────────────────────────────────────────────────

def variant_question(variants: Sequence[Dict]) -> Choice:
    """One choice over an item's approved variants, keyed by variant id with the text as the
    description, and `no_match`. Rewording it means bumping `VARIANT_VERSION`."""
    options = {str(v["variant_id"]): _norm(v["text"]) for v in list(variants)[:MAX_VARIANTS]}
    return Choice(
        VARIANT_VERSION,
        "Which approved resume bullet best fits this job? Read the job's requirements and choose "
        "the bullet that shows the kind of work, skills and results the job asks for, even when "
        "it is worded differently from the posting. A bullet that only repeats the posting's "
        "keywords while describing a different kind of role does not fit. Choose no_match when "
        "none of the bullets shows work the job asks for.",
        options, no_match=NO_MATCH)


def track_key(track: str) -> str:
    """The option name for a track: its own name, unless that would collide with the catch-all."""
    return track if track != NONE else f"{track} (track)"


def track_description(track: str, job_title: Optional[str]) -> str:
    words = _norm(track.replace("_", " "))
    title = _norm(job_title)
    return (f"A saved resume track for {words} roles, started from a job titled \"{title}\"."
            if title else f"A saved resume track for {words} roles.")


def track_question(tracks: Sequence[Dict]) -> Tuple[Choice, Dict[str, str]]:
    """`(question, track_of)`: one choice over the saved tracks (`[{track, title}]`) and `none`,
    and the track behind each option name. Rewording it means bumping `BASELINE_VERSION`."""
    options: Dict[str, Optional[str]] = {}
    track_of: Dict[str, str] = {}
    for t in list(tracks)[:MAX_TRACKS]:
        key = track_key(t["track"])
        options[key] = track_description(t["track"], t.get("title"))
        track_of[key] = t["track"]
    q = Choice(
        BASELINE_VERSION,
        "Which saved resume track should this job's resume start from? Each track is the resume "
        "the candidate built for one kind of role. Choose the track whose role matches the work "
        "this job actually asks for, judging by its requirements more than by the job title "
        "alone. Choose none when no saved track matches the job.",
        options, no_match=NONE)
    return q, track_of


# ── answers → a decision ─────────────────────────────────────────────────────

@dataclass
class Decision:
    """What Jev (or the cache) decided. `pick` is the chosen variant id or track name, or None for
    `no_match` / `none` (below the threshold included). `top` is the best real option whatever the
    threshold said (Jev's own choice when that is a real option, else the likeliest real option) and
    `top_p` its probability; `argmax` is Jev's own choice (None when it chose the catch-all), `mass` the
    probability it puts on any real option (one minus the catch-all), `p` the picked (else top) option's
    own probability, and `propensity` the whole distribution."""
    pick: Optional[str]
    top: Optional[str]
    top_p: Optional[float]
    p: Optional[float]
    propensity: Dict[str, float]
    source: str                         # jev | cache
    model: Optional[str] = None
    catch_all_p: Optional[float] = None
    argmax: Optional[str] = None
    mass: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"pick": self.pick, "top": self.top, "top_p": self.top_p, "p": self.p,
                "mass": self.mass, "propensity": dict(self.propensity), "source": self.source}


def _distribution(answer: Answer, options: Sequence[str]) -> Dict[str, float]:
    probs = {str(k): float(v) for k, v in (answer.probabilities or {}).items()}
    if not probs and answer.value is not None:
        probs = {str(answer.value): float(answer.p if answer.p is not None else 1.0)}
    return {o: _r(probs.get(o, 0.0)) or 0.0 for o in options}


def _decision(answer: Answer, ids: Sequence[str], catch_all: str, tau: float, gate: str,
              track_of: Optional[Dict[str, str]] = None) -> Optional[Decision]:
    """The answer as a `Decision`, or None when it came from the fallback. `gate` says what must reach
    `tau` for a pick: `mass` (the probability Jev puts on any real option, 1 - p(catch-all); the pick is
    the likeliest real option) or `argmax` (Jev's own choice must be a real option and its probability
    reach `tau`)."""
    if answer.fell_back:
        return None
    named = (lambda k: (track_of or {}).get(k, k)) if track_of else (lambda k: k)
    dist = _distribution(answer, [*ids, catch_all])
    real = {k: v for k, v in dist.items() if k != catch_all}
    chosen = str(answer.value)
    top = (chosen if chosen != catch_all
           else max(real, key=lambda k: (real[k], k)) if real else None)
    mass = _r(1.0 - dist.get(catch_all, 0.0))
    if gate == "mass":
        ok = top is not None and (mass or 0.0) >= tau - 1e-9
    else:
        ok = chosen != catch_all and answer.p is not None and answer.p >= tau - 1e-9
    pick = top if ok else None
    p = real.get(top) if gate == "mass" and top else _r(answer.p)
    return Decision(
        pick=named(pick) if pick else None, top=named(top) if top else None,
        top_p=real.get(top) if top else None, p=p,
        propensity={named(k): v for k, v in dist.items()}, source=answer.source,
        model=answer.model, catch_all_p=dist.get(catch_all),
        argmax=named(chosen) if chosen != catch_all else None, mass=mass)


# ── asking ───────────────────────────────────────────────────────────────────

def ask_variant(title: str, requirements: Optional[Iterable[Dict]], item_title: str,
                variants: Sequence[Dict], *, tau: Optional[float] = None,
                client=None) -> Optional[Decision]:
    """Which approved variant of an item fits the job: a `Decision`, or None when Jev did not
    answer (no key, mode `off`, an API error) and the caller falls back. Asks nothing when the
    item has no variant. A replay miss raises `JevReplayMiss`."""
    if not variants:
        return None
    from harness.decisions import engine

    q = variant_question(variants)
    (answer,) = engine.decide(VARIANT_POINT, variant_state(title, requirements, item_title), [q],
                              fallback=None, client=client)
    ids = [str(v["variant_id"]) for v in list(variants)[:MAX_VARIANTS]]
    return _decision(answer, ids, NO_MATCH, TAU_VARIANT if tau is None else tau, "mass")


def ask_track(title: str, requirements: Optional[Iterable[Dict]], tracks: Sequence[Dict], *,
              tau: Optional[float] = None, client=None) -> Optional[Decision]:
    """Which saved track the job starts from: a `Decision` whose `pick` is a track name or None
    (`none`), or None when Jev did not answer. Asks nothing when no track is saved."""
    if not tracks:
        return None
    from harness.decisions import engine

    q, track_of = track_question(tracks)
    (answer,) = engine.decide(BASELINE_POINT, baseline_state(title, requirements), [q],
                              fallback=None, client=client)
    return _decision(answer, list(track_of), NONE, TAU_BASELINE if tau is None else tau, "argmax",
                     track_of=track_of)
