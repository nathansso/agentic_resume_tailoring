"""The memory gate (#202): is a user message a standing preference, and what is it?

A host left to itself under-calls memory tools (When2Tool, #105), so `art hook user-prompt`
runs `observe` on every message. This module is the decision behind it. It never writes: it
returns a routing decision, and `harness/memory.py` acts on it.

**Two stages.**

1. **The prefilter** (deterministic, `prefilter`). Cheap cues ("always", "never", "don't",
   "prefer", "make sure", "from now on", "for this job", ...) mark a message as a candidate. A
   non-candidate is dropped with no Jev call. The prefilter also sets `negated` from negation
   cues: Jev is documented as weak on negation, so the flag is the backstop for its direction
   answer. A message over `MAX_CHARS` is taken to be pasted material (a posting, a draft) and
   dropped; the host can still call `observe` or `record_preference` itself.
2. **The Jev gate**, `memory_gate@v1`: one request per candidate carrying four questions about
   the message, all worded positively, over the message text alone (the state is `{message}`; v2,
   below, adds the previous assistant turn):
   - *standing*: a `noul`: "Does this message state a lasting preference about how the user's
     resume should be written or what it should include?" A one-off edit request, a question, a
     fact about the user's experience and small talk are named as not one.
   - *direction*: a `choice` of `emphasize`, `suppress`, `format_rule`, `none`.
   - *strength*: a `score` of 5 levels, the #129 1-5 scale in its own wording.
   - *target*: a `choice` over the user's catalog (every skill, role and project, and the
     sections) plus `no_match`. Jev picks from the list; it cannot name a target.

**Routing** (`route`):

- p below `TAU_LO`: **drop**.
- p in `[TAU_LO, TAU_HI)`, or any condition below failing: **host**. The `user-prompt` hook adds
  one line asking the host to confirm with the user and call `record_preference`, with the
  gate's guess.
- p at or above `TAU_HI` with an emphasize or suppress direction, a target Jev picked with
  p >= `TAU_TARGET` and the message names in words (`names_target`: Jev picks from the list even
  for "leave my GPA off", which is not the education section), a direction the prefilter's
  `negated` flag agrees with, a strength of 4 or less, one statement, a global scope or a known
  job, and a message of at most `MAX_AUTO_TEXT` characters: **write**. Format rules always go to
  the host (it records them with polarity `reframe`).

**The gate never writes a preference about a whole section** (`section:*` targets). A misread
one suppresses or reorders a whole section, so it goes to the host, like a strength-5
preference: a code rule, not a threshold. Only item-level targets (a skill, a role, a
project) are written.

**The gate never writes a strength-5 preference.** A strength 5 (and every negative pin, which is a
strength-5 suppression) becomes a gate that refuses plans (#129, #198), so it always goes to
the host with the guess, for the user to confirm through `record_preference`. This is a safety
rule, not a threshold: `route` checks it before anything is written.

**`memory_gate@v2`: the previous assistant turn (#244).** A message such as "never list that again" or
"keep it like that" cannot be read without knowing what "that" is. `unresolved_reference` flags those (an
anaphoric phrase, a bare pronoun when the message names no catalog item, a short yes/no reply), and only
then does `observe` look for the previous assistant turn (`harness/transcript.py` reads it from the
host's transcript, or the host passes it). With a turn, the same four questions run as
`memory_gate@v2`: the state is `{message, previous_assistant_turn}` and each question says the turn is
there only to resolve what the message refers to. The routing and every code rule are the same; the
thresholds (`TAU_*_V2`) are fitted separately on `eval/memory_gate_labels/context/`. With no reference,
or no readable turn, v1 runs exactly as before: the same state, cache keys and thresholds.

**With no key, mode `off` or an API error** the prefilter alone decides: every candidate goes to
the host, nothing is written, and non-candidates are dropped.

Thresholds are fitted on `eval/memory_gate_labels/` (see the comment on the constants), not
taken from Jev's reported confidence. Model-free apart from `engine.decide`, imported only when
a message is asked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from harness.decisions.questions import Answer, Choice, Noul, Score, canonical

POINT = "memory_gate"
VERSION = "memory_gate@v1"
VERSION_V2 = "memory_gate@v2"      # the same questions, with the previous assistant turn in the state (#244)
VERSIONS = (VERSION, VERSION_V2)
PREVIOUS_MAX = 600                 # characters of the previous assistant turn that are sent (its end)

DIRECTIONS = ("emphasize", "suppress", "format_rule", "none")
NO_MATCH = "no_match"
PIN_STRENGTH = 5            # a strength-5 preference is a hard gate: never written by the gate
AUTO_MAX_STRENGTH = PIN_STRENGTH - 1
# The code rules, each of which only ever removes a write. Thresholds are fitted as if they did not exist
# (`eval/fit_memory_gate_threshold.py`): they are defence in depth, never grounds for a looser threshold.
CODE_RULES = ("hard", "section", "backstop", "named")
HI_FLOOR = 0.5              # a write needs Jev to call the message more likely than not a lasting preference
HARD_MASS = 0.25            # a safety margin, not a fit: this much probability on level 5 is enough to ask the user
MAX_CHARS = 1200            # a longer message is pasted material, not a statement
MAX_AUTO_TEXT = 300         # a longer message is not stored verbatim as the preference text
MAX_CATALOG = 254           # a choice takes at most 255 options, one of them no_match
_ROUND = 4

# Fitted (#202) on the 111 user-confirmed synthetic messages in eval/memory_gate_labels/ (67 standing
# preferences, 44 not), against jev-1.13.0's recorded answers to memory_gate@v1, scoring by Jev's
# yes-probability. Refit with `python eval/fit_memory_gate_threshold.py analyze` whenever the model
# or a question changes. The rules (`recommend`) are in priority order, each then taking the grid
# value closest to the middle of its gap, ties to the higher. **TAU_HI is fitted as if the code rules
# (CODE_RULES: strength 5, HARD_MASS, the section rule, the negation backstop) did not exist**: they
# only ever remove writes, are defence in depth, and are never grounds for a looser threshold. It is
# also floored at HI_FLOOR, so a write needs Jev to call the message more likely than not a preference.
#   TAU_TARGET 0.75: no wrong binding among the true preferences Jev binds to an item the message
#     names (55 right, 1 wrong: en_coursework, 0.63), then the most right ones. All 55 right named
#     bindings score >= 0.85. The name check (`names_target`) already removes en_gpa (0.92, bound to
#     the education section) and fr_past_tense (0.87, a tense rule bound to the experience section).
#   TAU_HI 0.65: 0 wrong of 29 writes without the code rules. The highest would-be wrong write is
#     js_edu_first (0.63, a section target where the label says format rule and Jev says emphasize)
#     and the lowest right write is 0.68: the gap is 0.05 wide, and every grid value from 0.50 to 0.60
#     writes it. With the code rules on the gate writes 15, none wrong.
#   TAU_LO 0.25: 0 of the 57 true preferences Jev is asked about are dropped (the lowest is
#     js_rivermount, 0.31) and 0 non-preferences land between TAU_LO and TAU_HI (the highest
#     non-preference scores 0.17).
TAU_LO = 0.25
TAU_HI = 0.65
TAU_TARGET = 0.75

# memory_gate@v2 (#244): fitted on eval/memory_gate_labels/context/ (40 messages, 33 standing preferences),
# v2's own answers, by the same rules as above (`python eval/fit_memory_gate_context.py analyze`), TAU_HI
# again as if the code rules did not exist and again floored at HI_FLOOR. **The pin rule:** a write
# threshold of the context variant is never looser than the evidence-backed v1 one while the variant's own
# write set is thinner than v1's: TAU_HI_V2 = max(fit, TAU_HI) and TAU_TARGET_V2 = max(fit, TAU_TARGET)
# unless the set holds at least V1_WOULD_BE_WRITES would-be writes (v1's own, with the code rules off).
# The reason is that a threshold fitted on three would-be writes sits wherever the rule leaves it (the
# floor), which is not evidence that a looser cut is safe. TAU_LO is not a write threshold and is fitted.
#   TAU_HI_V2 0.65: the fit alone gives 0.50 (0 wrong of 3 writes without the code rules, the lowest right
#     one scores 0.84, so every grid value from the floor to 0.80 is equal); 3 < 29, so v1's 0.65 holds.
#   TAU_TARGET_V2 0.75: the fit alone gives 0.40 (3 named bindings, all right and >= 0.83, none wrong, so
#     nothing bounds it from below); pinned to v1's 0.75. The five wrong bindings in the set are all items
#     the message does not name; the name check owns them.
#   TAU_LO_V2 0.20: 0 of the 33 true preferences are dropped (the lowest is cx_looker_stop, 0.32).
V1_WOULD_BE_WRITES = 29     # #202's writes at its thresholds with the code rules off (REPORT.md: 29)
TAU_LO_V2 = 0.20
TAU_HI_V2 = 0.65
TAU_TARGET_V2 = 0.75


def thresholds(version: str = VERSION) -> Tuple[float, float, float]:
    """`(tau_lo, tau_hi, tau_target)` for a question version."""
    return (TAU_LO_V2, TAU_HI_V2, TAU_TARGET_V2) if version == VERSION_V2 else (TAU_LO, TAU_HI, TAU_TARGET)


# ── the prefilter ────────────────────────────────────────────────────────────

# Cues of a standing statement. Written from the design's list, not tuned on the labelled set, so
# its recall on the set is a fair measure of what the prefilter costs.
_CUES: Dict[str, str] = {
    "always": r"\balways\b",
    "never": r"\bnever\b",
    "don't": r"\bdon'?t\b",
    "do not": r"\bdo not\b",
    "stop": r"\bstop\b",
    "avoid": r"\bavoid",
    "prefer": r"\bprefer",
    "make sure": r"\bmake sure\b",
    "keep": r"\bkeep\b",
    "i want": r"\bi want\b",
    "i'd rather": r"\bi(?: would|'d) rather\b",
    "from now on": r"\bfrom now on\b|\bgoing forward\b|\bin future\b|\bin the future\b",
    "every resume": r"\b(?:every|all|any) (?:of my )?(?:resumes?|versions?|applications?)\b",
    "leave out": r"\bleave (?:\w+ ){0,4}(?:out|off)\b|\bkeep (?:\w+ ){0,4}(?:out|off)\b",
    "omit": r"\bomit|\bexclude|\bskip\b|\bno more\b|\bno longer\b|\bfree of\b",
    "lead with": r"\blead(?:ing)? with\b|\bemphasi[sz]e|\bhighlight|\bdownplay|\bplay down\b",
    "i like": r"\bi (?:really )?(?:like|love|hate|dislike)\b|\bi'?m not (?:a fan|into)\b",
    "should": r"\bshould(?:n't)?\b",
    "for this job": r"\b(?:for|on|in) this (?:\w+ ){0,2}(?:job|role|application|position|posting|"
                    r"opening|resume|one|time)\b|\bthis time\b|\b(?:just|only) for (?:this|that)\b",
}
_CUE_RES = {name: re.compile(rx, re.IGNORECASE) for name, rx in _CUES.items()}

# What reads as a wish to leave something out. A message with one of these and an emphasize
# direction (or a suppress direction with none of them) is one Jev may have misread.
_NEGATION = re.compile(
    r"\b(?:never|not|no|nor|neither|without|stop|hide|drop|remove|cut|ditch|delete|"
    r"avoid\w*|skip\w*|omit\w*|exclude\w*|rather not|no longer|no more|free of)\b|n't\b"
    r"|\bleave (?:\w+ ){0,4}(?:out|off)\b|\bkeep (?:\w+ ){0,4}(?:out|off)\b"
    r"|\btake (?:\w+ ){0,4}out\b",
    re.IGNORECASE)

# The scope the wording claims. A job scope needs the job to be known; a role scope ("for ML
# roles") cannot be recorded by the gate, which sets no role family.
_JOB_SCOPE = re.compile(_CUES["for this job"], re.IGNORECASE)
_ROLE_SCOPE = re.compile(
    r"\bfor (?!this\b|that\b|the (?:current|this)\b)(?:[\w\-/&+]+ ){1,4}"
    r"(?:roles?|positions?|jobs?|applications?|postings?|internships?)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _norm(text: Any) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


@dataclass(frozen=True)
class Prefilter:
    candidate: bool
    negated: bool = False
    scope: Optional[str] = None          # "job", "role" or None
    statements: int = 0                  # sentences that carry a cue
    cues: Tuple[str, ...] = ()
    reason: Optional[str] = None         # why a non-candidate was dropped

    def as_dict(self) -> Dict[str, Any]:
        return {"candidate": self.candidate, "negated": self.negated, "scope": self.scope,
                "statements": self.statements, "cues": list(self.cues)}


def prefilter(text: str) -> Prefilter:
    """Whether `text` looks like a preference candidate, and whether it reads as negated."""
    text = _norm(text)
    if not text:
        return Prefilter(False, reason="empty")
    if len(text) > MAX_CHARS:
        return Prefilter(False, reason="too_long")
    cues = tuple(name for name, rx in _CUE_RES.items() if rx.search(text))
    if not cues:
        return Prefilter(False, reason="no_cue")
    statements = sum(any(rx.search(s) for rx in _CUE_RES.values())
                     for s in _SENTENCE.split(text) if s.strip())
    scope = "job" if _JOB_SCOPE.search(text) else "role" if _ROLE_SCOPE.search(text) else None
    return Prefilter(True, negated=bool(_NEGATION.search(text)), scope=scope,
                     statements=max(statements, 1), cues=cues)


def heuristic_guess(pre: Prefilter) -> Dict[str, Any]:
    """What the prefilter alone says, for the comparison against Jev: a candidate is a
    preference; the direction is suppress when negated, else emphasize."""
    return {"is_preference": pre.candidate,
            "direction": ("suppress" if pre.negated else "emphasize") if pre.candidate else "none",
            "job_scoped": pre.scope == "job"}


# ── an unresolved reference (#244) ───────────────────────────────────────────

# A message that depends on the turn before it. Three kinds of cue:
#  - an anaphoric phrase, which always points back ("again", "the same", "like that", "that way");
#  - a bare pronoun ("that", "it", "this", "those", "them"), which points back only when the message
#    does not itself name a catalog item ("Don't ever list Looker, I barely used it" has its antecedent);
#  - a short reply that answers a question ("Yes, always.", "Don't, ever.").
# An ordinal referent ("the last one", "the second one") counts as a pronoun. A complementiser "that" ("make
# sure that my GPA is off") is not one. The cue list was written before the new context messages were recorded
# and checked against the 111 self-contained messages of the main set (a flag there is a false detection); the
# ordinal referents were added after the recording showed three misses (`cx_mis_*`), so detection recall on
# that set is not an out-of-sample figure.
_ANAPHORA = re.compile(
    r"\bagain\b|\bthe same\b|\bsame (?:as|way|thing)\b|\bas before\b|\blike before\b|\blast time\b"
    r"|\bas (?:you|we) (?:did|had)\b|\blike (?:that|this)\b|\b(?:that|this) way\b", re.IGNORECASE)
_PRONOUN = re.compile(r"\b(?:that|this|those|these|them|it)(?: ones?)?\b"
                      r"|\b(?:the )?(?:first|second|third|last|middle|other|latter|former) ones?\b", re.IGNORECASE)
_COMPLEMENTISER = re.compile(
    r"\b(?:make sure|ensure|so|say|said|think|know|mean|means|note|show|shows|sure|such|given|insist)\s+that\b"
    r"|\bthat\s+(?:my|the|i|you|your|we|it|every|all|any|each|a|an|no|there|this|those)\b"
    r"|\b(?:something|anything|everything|bullets?|skills?|things?|projects?|roles?|items?|words|jobs|"
    r"sections?|lines?|tools)\s+that\b",
    re.IGNORECASE)
# A reply: opens with an answer word and is short, or is nothing but answer and adverb words ("Don't, ever.").
_REPLY_OPENER = re.compile(r"^(?:yes|yeah|yep|yup|nope|sure|ok|okay|right|exactly|correct|fine"
                           r"|no(?! (?:more|longer|less|fewer|one|other))|good(?! (?:morning|afternoon|evening))"
                           r"|great)\b", re.IGNORECASE)
_REPLY_WORDS = frozenset("please do don't dont never always ever definitely absolutely not".split())
REPLY_WORDS = 10                      # an opener-led reply is at most this many words


def unresolved_reference(text: str, catalog: Optional[Sequence[Dict]] = None) -> List[str]:
    """The cues in `text` that make it depend on the previous assistant turn, or `[]` when it reads on its
    own. `catalog` lets a bare pronoun resolve inside the message: one that names a catalog item has its
    antecedent ("Leave Looker off, I barely used it")."""
    plain = _norm(text)
    if not plain:
        return []
    cues = [m.group(0).lower() for m in _ANAPHORA.finditer(plain)]
    pronouns = [m.group(0).lower() for m in _PRONOUN.finditer(_COMPLEMENTISER.sub(" ", plain))]
    if pronouns and not (catalog and any(names_target(plain, e) for e in catalog)):
        cues += pronouns
    words = re.findall(r"[\w'/+]+", plain.lower())
    if not cues and words and ((len(words) <= REPLY_WORDS and _REPLY_OPENER.match(plain))
                               or all(w in _REPLY_WORDS for w in words)):
        cues.append("short reply")
    return cues


# ── the questions ────────────────────────────────────────────────────────────

STRENGTH_LEVELS = [
    "1: a passing remark; the user would not mind either way",
    "2: a mild preference, mentioned lightly or hedged",
    "3: a clear preference, stated plainly without insisting",
    "4: a firm preference the user stresses, such as 'always' or 'make sure'",
    "5: an absolute rule the user states as non-negotiable, such as 'never' or "
    "'under no circumstances'",
]


# What v2 adds to each question's instructions (#244). The state then also holds the assistant's previous
# turn; it is there only so that "that", "it" and "again" can be resolved. It is never the user's own words.
CONTEXT_NOTE = (" The state also holds the assistant's previous turn. Use it only to work out what the "
                "message refers to (what 'that', 'it', 'this' or 'again' points at, or what a short "
                "yes or no answers). Judge only what the user's own message says: the assistant's turn "
                "is not the user's wish, and an item it mentions that the message does not refer to "
                "is not part of the answer.")


def _note(version: str) -> str:
    return CONTEXT_NOTE if version == VERSION_V2 else ""


def question_standing(version: str = VERSION) -> Noul:
    return Noul(
        version,
        "Does this message state a lasting preference about how the user's resume should be "
        "written or what it should include? Answer yes when the user states a rule they want "
        "applied from now on, to every version of their resume or to the resume for one named "
        "job: something to leave out, to feature, to always do, or a format they want. A "
        "request to change one particular bullet or section right now (shorten it, reword it, "
        "fix it), a question, a fact about the user's own experience, a reaction, and small "
        "talk are not lasting preferences." + _note(version),
        true="The message states a lasting preference about the resume.",
        false="The message is a one-off request, a question, a fact, a reaction or small talk.")


def question_direction(version: str = VERSION) -> Choice:
    return Choice(
        version,
        "What does the user want done with their resume? Read negations carefully: a user who "
        "says not to leave something out, or not to stop including it, wants it included "
        "(emphasize); a user who says not to include something wants it left out (suppress). "
        "Choose none when the message states no preference about the resume." + _note(version),
        {"emphasize": "The user wants something featured, led with, kept, or always included.",
         "suppress": "The user wants something left out or never mentioned.",
         "format_rule": "The user wants the resume written or laid out a certain way, such as "
                        "its length, wording style, tense or structure, without naming "
                        "something to include or leave out.",
         "none": "The message states no preference about the resume."})


def question_strength(version: str = VERSION) -> Score:
    return Score(
        version,
        "How firmly does the user hold this preference about their resume? Judge the user's own "
        "wording, not how important the topic is. Level 5 is only for an absolute rule stated "
        "as non-negotiable, such as 'never' or 'under no circumstances'." + _note(version),
        STRENGTH_LEVELS)


def catalog_options(catalog: Sequence[Dict]) -> Tuple[Dict[str, Optional[str]], Dict[str, str]]:
    """`(options, key_of)`: the target question's options (name -> description) and the catalog
    key behind each name. A name is the entry's label; a repeated label is told apart by its key."""
    options: Dict[str, Optional[str]] = {}
    key_of: Dict[str, str] = {}
    for entry in list(catalog)[:MAX_CATALOG]:
        label = _norm(entry.get("label") or entry.get("key"))
        name = label if label not in options and label != NO_MATCH else f"{label} ({entry['key']})"
        kind, _, bare = label.partition(": ")
        bare = bare or label
        desc = {"skill": f"The skill or tool {bare}.", "project": f"The project {bare}.",
                "experience": f"The job {bare.replace(' @ ', ' at ')}.",
                "section": f"The {bare.replace('section: ', '')} section of the resume."}.get(
                    entry.get("target_type") or kind, f"{bare}.")
        options[name] = desc
        key_of[name] = entry["key"]
    return options, key_of


def question_target(catalog: Sequence[Dict], version: str = VERSION) -> Choice:
    options, _ = catalog_options(catalog)
    return Choice(
        version,
        "Which item on the user's resume does this message ask to feature, leave out or "
        "arrange? Choose the listed item the message is about, even when it names it only "
        "loosely. Choose no_match when the message is about something not listed, about the "
        "resume in general, or does not clearly refer to a listed item." + _note(version),
        options, no_match=NO_MATCH)


def clip_previous(previous: Optional[str]) -> str:
    """The previous assistant turn as the state carries it: whitespace collapsed, and when it is longer
    than `PREVIOUS_MAX` its last characters, behind an ellipsis (a reply answers the end of the turn)."""
    text = _norm(previous)
    return text if len(text) <= PREVIOUS_MAX else "…" + text[-(PREVIOUS_MAX - 1):]


def build_state(text: str, previous: Optional[str] = None) -> Dict[str, Any]:
    """The v1 state: the message alone. With `previous` it is the key #202 measured the turn under
    (`previous_assistant`, the v1 questions): kept only so that measurement still replays, never a product
    path; v2 is `build_state_v2`."""
    state: Dict[str, Any] = {"message": _norm(text)}
    if previous and _norm(previous):
        state["previous_assistant"] = _norm(previous)[:600]
    return state


def build_state_v2(text: str, previous: str) -> Dict[str, Any]:
    """The v2 state: `{message, previous_assistant_turn}`."""
    return {"message": _norm(text), "previous_assistant_turn": clip_previous(previous)}


def questions_for(catalog: Sequence[Dict], version: str = VERSION) -> List[Any]:
    """The four questions, in the order `parse_answers` reads them."""
    return [question_standing(version), question_direction(version), question_strength(version),
            question_target(catalog, version)]


# ── answers → a guess ────────────────────────────────────────────────────────

@dataclass
class Guess:
    p: float                              # P(standing preference)
    direction: str
    direction_p: Optional[float]
    strength: int                         # 1-5
    strength_confidence: Optional[float]
    hard_p: float                         # probability Jev puts on level 5
    target_key: Optional[str]             # None when no_match
    target_label: Optional[str]
    target_p: Optional[float]
    target_named: bool                    # the message says the target's name
    source: str                           # jev | cache
    model: Optional[str] = None
    version: str = VERSION                # the question version that produced it: the thresholds follow it

    def as_dict(self) -> Dict[str, Any]:
        return {"p": self.p, "direction": self.direction, "direction_p": self.direction_p,
                "strength": self.strength, "hard_p": self.hard_p, "target": self.target_key,
                "target_label": self.target_label, "target_p": self.target_p,
                "target_named": self.target_named}


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), _ROUND)


def strength_of(answer: Answer) -> int:
    """The 1-5 strength behind a score answer (its value is the level's index)."""
    return max(1, min(5, int(float(answer.value) + 0.5) + 1))


_STOP_WORDS = frozenset("the of and for inc llc ltd corp co company at a an".split())


def _phrase(words: str) -> "re.Pattern":
    return re.compile(r"(?<![a-z0-9])" + re.escape(words.lower()) + r"(?:s|es)?(?![a-z0-9])")


def names_target(text: str, entry: Dict[str, str]) -> bool:
    """Whether the message names the item `entry` in words. Jev picks a target from the list even
    when the message only touches it ("leave my GPA off" -> the education section), and a wrong
    binding silently suppresses the wrong item, so the gate also requires the message to say
    the item's name: a skill, project, role (its title, or its company's first word) or section,
    as a whole word or phrase. A message that refers to an item loosely goes to the host."""
    hay = _norm(text).lower()
    label = _norm(entry.get("label"))
    bare = label.partition(": ")[2] or label
    kind = entry.get("target_type")
    if kind == "section":
        return bool(_phrase(bare.replace("section: ", "")).search(hay))
    if kind == "experience":
        title, _, company = bare.partition(" @ ")
        first = next((w for w in re.findall(r"[a-z0-9]+", company.lower()) if w not in _STOP_WORDS), "")
        return bool(title and _phrase(title).search(hay)) or bool(first and _phrase(first).search(hay))
    return bool(_phrase(bare).search(hay))


def parse_answers(answers: Sequence[Answer], catalog: Sequence[Dict],
                  text: str = "", version: str = VERSION) -> Optional[Guess]:
    """The four answers as a `Guess`, or None when any came from the fallback. `text` is the
    message, to check that it names the target Jev picked (never the previous turn: a name there is
    not the user's). `version` is the question version the answers are to."""
    if len(answers) != 4 or any(a.fell_back or a.p is None and a.kind == "noul" for a in answers):
        return None
    standing, direction, strength, target = answers
    _, key_of = catalog_options(catalog)
    key = key_of.get(str(target.value)) if target.value != NO_MATCH else None
    label = next((n for n, k in key_of.items() if k == key), None) if key else None
    entry = next((c for c in catalog if c["key"] == key), None) if key else None
    sources = {a.source for a in answers}
    return Guess(
        p=_r(standing.p), direction=str(direction.value), direction_p=_r(direction.p),
        strength=strength_of(strength), strength_confidence=_r(strength.confidence),
        hard_p=_r(strength.p_of("4")) or 0.0,
        target_key=key, target_label=label, target_p=_r(target.p),
        target_named=bool(entry and names_target(text, entry)),
        source="jev" if "jev" in sources else "cache", model=standing.model, version=version)


def unavailable_reason(answers: Sequence[Answer]) -> Optional[str]:
    """Why Jev did not answer (`no_key`, `mode=off`, `api_error:...`), or None when it did."""
    for a in answers:
        if a.fell_back:
            return a.reason or "fallback"
        if a.p is None and a.kind == "noul":
            return "bad_answer"
    return None


def version_for(previous: Optional[str]) -> str:
    """The question version a message is asked under: v2 when there is a previous turn to send, else v1."""
    return VERSION_V2 if previous and _norm(previous) else VERSION


def ask(text: str, catalog: Sequence[Dict], previous: Optional[str] = None, *, client=None,
        version: Optional[str] = None):
    """The four answers for one message, through the engine (cache, then Jev, then the fallback).
    With a previous turn the questions are `memory_gate@v2` and the state carries the turn; without one
    they are v1 over the message alone. `version=VERSION` with a turn is #202's measurement (the v1
    questions over the legacy state), kept so its recordings replay."""
    from harness.decisions import engine

    version = version or version_for(previous)
    state = build_state_v2(text, previous or "") if version == VERSION_V2 else build_state(text, previous)
    return engine.decide(POINT, state, questions_for(catalog, version), fallback=None, client=client)


# ── routing ──────────────────────────────────────────────────────────────────

HOST_LINE = ("ART: this message may be a standing preference; confirm with the user and call "
             "record_preference if so.")

_WHY = {
    "uncertain": "the gate is not sure it is a lasting preference",
    "jev_unavailable": "the memory gate's Jev check did not run, so only its cue words decided",
    "no_target": "it could not tell which item the message is about",
    "negation_disagrees": "the message's negation and the direction Jev read do not agree",
    "hard_preference": "a strength-5 rule (such as a negative pin) becomes a hard gate on every "
                       "plan, so ART never saves one without the user's confirmation",
    "job_unknown": "it is about one job but ART does not know which job this session is on",
    "role_scope": "it is about one kind of role, and the gate cannot set a role family",
    "multiple_statements": "the message states more than one thing",
    "format_rule": "format rules are recorded by the host with polarity reframe",
    "no_direction": "Jev found no clear emphasize or suppress direction",
    "section_target": "it is about a whole section, which a wrong reading would suppress or "
                      "reorder wholesale, so the user confirms it",
    "long_message": "the message is too long to store as the preference text",
    "read_only": "this ART process may not write",
    "would_supersede": "it would replace a preference the user already holds",
}


def explain(decision: Dict[str, Any]) -> str:
    """The `user-prompt` hook's line for the host, or '' when there is nothing to say."""
    action, guess = decision["action"], decision.get("guess") or {}
    if action == "write":
        return (f"ART saved a standing preference from this message: "
                f"\"{decision.get('text') or ''}\" ({guess.get('direction')} "
                f"{guess.get('target') or ''}, strength {guess.get('strength')}, "
                f"{decision.get('scope', 'global')} scope). Mention it to the user in your reply; "
                "if it is wrong, correct it with record_preference.")
    if action != "host":
        return ""
    line = HOST_LINE
    bits = []
    if guess:
        what = guess.get("direction")
        if guess.get("target"):
            what += f" {guess['target']}"
        bits.append(f"{what}, strength {guess.get('strength')}")
    elif decision.get("cues"):
        bits.append("it says " + ", ".join(f"\"{c}\"" for c in decision["cues"][:3]))
    why = _WHY.get(decision.get("reason") or "", "")
    if bits or why:
        line += " Gate guess: " + "; ".join(bits + ([why] if why else [])) + "."
    return line


def _decision(action: str, reason: str, pre: Prefilter, guess: Optional[Guess], text: str,
              scope: str = "global", source: Optional[str] = None) -> Dict[str, Any]:
    """`version` is the question version whose answers decided (`memory_gate@v1` or `@v2`), None when no
    Jev answer did (the prefilter settled it, or Jev was unavailable)."""
    return {"action": action, "reason": reason, "candidate": pre.candidate, "negated": pre.negated,
            "p": guess.p if guess else None,
            "source": source or (guess.source if guess else "prefilter"),
            "guess": guess.as_dict() if guess else None, "cues": list(pre.cues),
            "text": _norm(text), "scope": scope, "version": guess.version if guess else None}


def route_prefilter(text: str, pre: Prefilter) -> Optional[Dict[str, Any]]:
    """The decision the prefilter alone settles, or None when Jev should be asked: a
    non-candidate is dropped, and a message that states several things or a role-scoped one goes
    to the host without a call."""
    if not pre.candidate:
        return _decision("drop", pre.reason or "no_cue", pre, None, text)
    if pre.statements > 1:
        return _decision("host", "multiple_statements", pre, None, text)
    if pre.scope == "role":
        return _decision("host", "role_scope", pre, None, text)
    return None


def route(text: str, pre: Prefilter, guess: Optional[Guess], *, job_known: bool = False,
          write_ok: bool = True, unavailable: Optional[str] = None,
          tau_lo: Optional[float] = None, tau_hi: Optional[float] = None,
          tau_target: Optional[float] = None, without: Collection[str] = ()) -> Dict[str, Any]:
    """The routing decision for one message: `{action, reason, candidate, negated, p, source,
    guess, cues, text, scope}`. `action` is `drop`, `host` or `write`. Pure. `without` names code
    rules to skip (`CODE_RULES`); it exists only so the fit can choose thresholds as if those rules
    did not exist and measure what each one removes. Nothing in the product passes it."""
    v_lo, v_hi, v_tgt = thresholds(guess.version if guess else VERSION)
    lo = v_lo if tau_lo is None else tau_lo
    hi = v_hi if tau_hi is None else tau_hi
    tgt = v_tgt if tau_target is None else tau_target
    early = route_prefilter(text, pre)
    if early is not None:
        return early
    scope = "job" if pre.scope == "job" else "global"
    if guess is None:
        d = _decision("host", "jev_unavailable", pre, None, text, scope)
        d["unavailable"] = unavailable
        return d
    host = lambda reason: _decision("host", reason, pre, guess, text, scope)  # noqa: E731
    if guess.p < lo - 1e-9:
        return _decision("drop", "below_tau_lo", pre, guess, text, scope)
    if guess.p < hi - 1e-9:
        return host("uncertain")
    # p >= TAU_HI: written only when nothing below objects.
    if "hard" not in without and (guess.strength >= PIN_STRENGTH or guess.hard_p >= HARD_MASS - 1e-9):
        return host("hard_preference")
    if "section" not in without and (guess.target_key or "").startswith("section:"):
        return host("section_target")
    if guess.direction == "format_rule":
        return host("format_rule")
    if guess.direction not in ("emphasize", "suppress"):
        return host("no_direction")
    if (guess.target_key is None or ("named" not in without and not guess.target_named)
            or (guess.target_p or 0.0) < tgt - 1e-9):
        return host("no_target")
    if "backstop" not in without and ((pre.negated and guess.direction != "suppress")
                     or (guess.direction == "suppress" and not pre.negated)):
        return host("negation_disagrees")
    if scope == "job" and not job_known:
        return host("job_unknown")
    if len(_norm(text)) > MAX_AUTO_TEXT:
        return host("long_message")
    if not write_ok:
        return host("read_only")
    return _decision("write", "auto", pre, guess, text, scope)


def label_of(entry: Dict[str, Any]) -> Dict[str, Any]:
    """A labelled message's truth, with the defaults a non-preference gets."""
    return {"is_preference": bool(entry["is_preference"]),
            "direction": entry.get("direction") or "none",
            "strength": entry.get("strength"), "target": entry.get("target"),
            "job_scoped": bool(entry.get("job_scoped"))}


def canonical_state(text: str, previous: Optional[str] = None) -> str:
    return canonical(build_state(text, previous))


# ── the catalog ──────────────────────────────────────────────────────────────

def make_catalog(skills: Sequence[str] = (), experiences: Sequence[Tuple[str, str]] = (),
                 projects: Sequence[str] = ()) -> List[Dict[str, str]]:
    """The catalog in `agents.preferences.target_catalog`'s shape (`{key, label, target_type}`),
    built without the model stack: sections, then roles, projects and skills, each in a stable
    order. Keys are the planner's (`exp:<title>|<company>`, `proj:<name>`, `skill:<name>`,
    `section:<name>`), which is what lets a stored preference bind straight onto a plan item."""
    from agents.preferences import SECTION_NAMES

    def k(value: Any) -> str:
        return str(value or "").strip().lower()

    out: List[Dict[str, str]] = [
        {"key": f"section:{s}", "label": f"section: {s}", "target_type": "section"}
        for s in SECTION_NAMES]
    out += [{"key": f"exp:{k(t)}|{k(c)}", "label": f"experience: {t} @ {c}", "target_type": "experience"}
            for t, c in sorted(experiences, key=lambda tc: (k(tc[1]), k(tc[0]))) if k(t)]
    out += [{"key": f"proj:{k(n)}", "label": f"project: {n}", "target_type": "project"}
            for n in sorted(projects, key=k) if k(n)]
    out += [{"key": f"skill:{k(n)}", "label": f"skill: {n}", "target_type": "skill"}
            for n in sorted(skills, key=k) if k(n)]
    seen, unique = set(), []
    for entry in out:                      # a repeated key is one target
        if entry["key"] not in seen:
            seen.add(entry["key"])
            unique.append(entry)
    return unique
