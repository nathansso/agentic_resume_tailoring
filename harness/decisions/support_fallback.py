"""The cited-bullet support check without Jev (#351): a deterministic reading of what a revised
bullet adds to its cited evidence.

`support.py` asks Jev whether the evidence backs the new bullet. With no key, `ART_JEV_MODE=off`
or an API error that question has no answer, and before #351 the bullet went unchecked: the #123
numbers-and-names gate blocks a new number or tool, and nothing semantic stood behind it (0 of the
54 labelled should-block pairs, `eval/FALLBACKS.md`). This module is that second line, model-free
and rule-based: small, explicit lexicons, every one listed below so a reviewer can read what it
does and does not know. Nothing here imports a model client (`tests/test_harness_boundary.py`).

**What it compares.** The revised bullet against its cited evidence, and, for leniency only, the
rest of the evidence a real item carries (its other source bullets and its header: title, company,
name). The bullet it revises (`original`) is not evidence, as in the Jev question: a claim that
only the original made is still unsupported, but one the user's own earlier text already carried
is a weaker signal than one this revision added, so it counts for half.

**Signals**, each with a weight. A strong signal (1.0) is a claim the evidence cannot make for the
bullet; a soft one (0.5) is a stretch that is often honest.

- *Role inflation*: a seniority or ownership word the evidence does not use (`led`, `owned`,
  `directed`, `architected`, `senior`, ...). `managed` and `designed` are soft, and strong when
  the evidence says the candidate only helped, contributed or assisted.
- *Invented scope*: reach and impact claims the evidence never makes (`entire`, `organization`,
  `end to end`, `fleet`, `high availability`, `outperforming`, `eliminating`, `used across`), a
  deployment or replacement the evidence does not describe (soft), a named method or model family
  the evidence does not name (`deep learning`, `k-means`), a tool from the knowledge graph's skill
  vocabulary the evidence never names (aliases folded: "torch" covers "PyTorch"), a vague quantity
  (`dozens`, `hundreds`) no number in the evidence reaches.
- *Contradiction* (strong, label `contradicts`): a negation the bullet drops ("without rewriting"
  to "rewrites"), an opposite pair (offline and remote, flat and significant, an image model
  turned into a text one), a migration read backwards, a verb pair that reverses the same object
  (`cut` and `increased`), and work credited to someone else.

**Outcome.** The score is the sum of the weights of the distinct signals. `>= TAU_BLOCK` is a
gate violation, `>= TAU_REVIEW` is shown to the host and user as `review`, else pass. The two
cutoffs are fitted on `eval/support_labels/pairs.json` by `eval/fit_support_fallback.py`, harmful
errors first (a false block of an honest edit is the worst error), and recorded in
`harness/policy.json` under the fallback's own model name, `fallback-support-v1`.

**What it cannot do.** It reads closed word lists, not meaning. A synonym it does not list for
the same fact reads as a new claim only if the word is on a list; a fabrication in other words
passes; a rewording that shares no listed word with the evidence passes. Jev reads all of those.
The cutoffs and the lists were fitted on 89 synthetic pairs, so they are a floor, not an accuracy
to expect on a real resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from agents.skill_postprocessor import SKILL_ALIASES
from harness import policy

MODEL = "fallback-support-v1"
VERSION = "support_fallback@v1"

# Fitted (#351) on the 89 pairs in eval/support_labels/ by `python eval/fit_support_fallback.py analyze`;
# the artifact records the fit (harness/policy.json). Scores are sums of STRONG and SOFT weights.
TAU_BLOCK = policy.get("support_fallback.tau_block")
TAU_REVIEW = policy.get("support_fallback.tau_review")

STRONG = 1.0
SOFT = 0.5
HALF = 0.5                 # a signal whose word the original bullet already carried counts for this share

ADDS, CONTRADICTS = "adds_unsupported", "contradicts"


# ── text ─────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""
a an and are as at be been being by for from has have had in into is it its of on or our over per
so than that the their them then there these they this those through to under until up was were
what when where which while who whom why will with within without would you your we us i me my he
she his her not no nor but if because about above after again against all also any both can could
did do does done each few how just may might more most much must other out own same should some
such too very via yet across among around before behind below between during off once only both
using used use based new
""".split())


def _stem(w: str) -> str:
    """A cheap stem so `rewriting`, `rewrites` and `rewrite` meet. Not linguistics: just enough
    that an inflected form of a word counts as that word."""
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    elif w.endswith("es") and len(w) > 4:
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        w = w[:-1]
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


class _Text:
    """One text, read once: lowercase tokens (apostrophes dropped, hyphens split), their stems,
    the padded normalized string phrases are matched in, and the numbers it states."""

    def __init__(self, text: str):
        self.raw = text or ""
        low = self.raw.lower().replace("'", "").replace("’", "")
        self.tokens: List[str] = _TOKEN.findall(low)
        self.set: FrozenSet[str] = frozenset(self.tokens)
        self.stems: FrozenSet[str] = frozenset(_stem(t) for t in self.tokens)
        # the skill alias map folds a spelling to its canonical name ("torch" is PyTorch), as
        # the term match does (agents/skill_matching.py), so a tool and its alias are one word
        self.folded: FrozenSet[str] = self.set | frozenset(
            SKILL_ALIASES[t].lower() for t in self.tokens if t in SKILL_ALIASES)
        self.norm = " " + " ".join(self.tokens) + " "
        self.low = low

    def phrase(self, p: str) -> bool:
        return f" {p} " in self.norm

    def any_phrase(self, phrases: Iterable[str]) -> bool:
        return any(self.phrase(p) for p in phrases)


def _norm_phrase(p: str) -> str:
    return " ".join(_TOKEN.findall(p.lower()))


_MAG = re.compile(r"(?<![a-z0-9.])(\d[\d,]*(?:\.\d+)?)\s?(k|m|b|thousand|million|billion)?(?![a-z0-9])")
_MAG_X = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def _numbers(text: str) -> List[float]:
    out = []
    for m in _MAG.finditer((text or "").lower()):
        try:
            out.append(float(m.group(1).replace(",", "")) * _MAG_X.get(m.group(2) or "", 1.0))
        except ValueError:
            continue
    return out


# ── signals ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Signal:
    kind: str                   # role | scope | method | quantity | addition | contradiction
    claim: str                  # what the bullet says that the evidence does not back
    weight: float
    label: str = ADDS

    def text(self) -> str:
        return f"{self.kind}: {self.claim}"


@dataclass(frozen=True)
class Claim:
    """A word or phrase the bullet may only use when the evidence uses it, or one of its
    `support` forms. `exact` compares the evidence's tokens as written (no stemming): the role
    words need it, since `owned` must not be satisfied by "their own"."""
    kind: str
    concept: str                                  # one score per concept, however many words trigger it
    weight: float
    triggers: FrozenSet[str] = frozenset()
    phrases: Tuple[str, ...] = ()
    support: FrozenSet[str] = frozenset()
    support_phrases: Tuple[str, ...] = ()
    exact: bool = False
    skip_next: FrozenSet[str] = frozenset()       # a trigger followed by one of these is another sense
    skip_prev: FrozenSet[str] = frozenset()


def _f(words: str) -> FrozenSet[str]:
    return frozenset(words.split())


def _p(*phrases: str) -> Tuple[str, ...]:
    return tuple(_norm_phrase(p) for p in phrases)


# Seniority and ownership. `lead` / `leads` is a verb only at the start of a bullet or after "and"
# / "to" ("lead" is also a noun: "lead generation", "team lead"), so it is read there alone.
_AUTHORITY = _f("""led owned owns owner ownership directed directs headed spearheaded spearheads oversaw oversees
architected principal""")
_AUTHORITY_OK = _AUTHORITY | _f("""lead leads leader leaders leading head director chief senior staff manager managed manages
managing founder cofounder captain president vp supervisor supervised supervising architect pioneered founded""")
_FOUNDING_OK = _f("""founded founder cofounder pioneered started start launched launch created create initiated established
formed organized built build""")

_CREATION = _f("""built build builds building wrote write writes writing created create creates developed develop develops
implemented implement implements engineered engineer designed design designs designing architected architect
architecture authored prototyped trained train constructed produced made make set stood stand""")

_HELPER = _f("helped assisted contributed participated shadowed supported")

_DEPLOY_OK = _f("""deployed deploy deploys deploying shipped ship ships shipping released release releases launched launch
launches delivered deliver delivers packaged package packages hosted host hosts serving served serves serve
production live published publish publishes docker container containers containerized kubernetes pushed
rolled productionized running runs ran""")

_REPLACE_OK = _f("""replaced replace replaces replacing swapped swap migrated migrate migrates migrating retired retire
supplanted superseded displaced""")

_CLAIMS: Tuple[Claim, ...] = (
    # role inflation
    Claim("role", "authority", STRONG, triggers=_AUTHORITY, support=_AUTHORITY_OK, exact=True,
          skip_next=_f("acyclic graph graphs edge edges component components axis axes eigenvalue eigenvalues")),
    Claim("role", "founding", STRONG, triggers=_f("founded pioneered"), support=_FOUNDING_OK | _AUTHORITY_OK, exact=True),
    Claim("role", "senior", STRONG, triggers=_f("senior"), support=_AUTHORITY_OK, exact=True,
          skip_next=_f("design capstone project thesis citizens citizen class year level")),
    Claim("role", "managed", SOFT, triggers=_f("managed manages managing"),
          support=_AUTHORITY_OK | _f("maintained maintain maintains maintaining ran run operated operate administered "
                                     "coordinated organized handled"), exact=True,
          skip_next=_f("service services database databases identity identities cluster clusters instance instances")),
    Claim("role", "designed", SOFT, triggers=_f("designed designs"), support=_CREATION, exact=True),
    Claim("role", "architecture", SOFT, triggers=_f("architecture architectures"),
          support=_f("architecture architectures architect architected design designed designs"), exact=True),
    Claim("role", "infrastructure", SOFT, triggers=_f("infrastructure"),
          support=_f("infrastructure iac"), exact=True),
    Claim("role", "platform", SOFT, triggers=_f("platform platforms ecosystem"),
          support=_f("platform platforms ecosystem"), exact=True),
    # invented scope: reach
    Claim("scope", "breadth", STRONG, triggers=_f("entire whole"),
          support=_f("entire whole all every each full complete"), exact=True),
    Claim("scope", "organization", STRONG, triggers=_f("organization organisation enterprise"),
          phrases=_p("company wide", "org wide", "department wide", "university wide", "campus wide",
                     "enterprise wide", "firm wide", "across the company", "across the department",
                     "across the university"),
          support=_f("organization organisation enterprise company org firm"), exact=True,
          skip_prev=_f("student")),
    Claim("scope", "adoption", STRONG, triggers=_f("adopted"),
          phrases=_p("used across", "used throughout", "rolled out across"),
          support=_f("adopted adoption rollout reused"),
          support_phrases=_p("used by", "used across", "used throughout", "rolled out", "reused by"), exact=True),
    Claim("scope", "used by", SOFT, phrases=_p("used by", "relied on by", "relied upon by", "rolled out to"),
          support=_f("adopted adoption rollout reused"),
          support_phrases=_p("used by", "used across", "used throughout", "rolled out", "reused by", "relied on by"),
          exact=True),
    Claim("scope", "end to end", STRONG, phrases=_p("end to end", "full stack", "fullstack"),
          support_phrases=_p("end to end", "full stack", "fullstack")),
    Claim("scope", "fleet", STRONG, triggers=_f("fleet fleets"), support=_f("fleet fleets"), exact=True),
    Claim("scope", "availability", STRONG,
          phrases=_p("high availability", "highly available", "zero downtime", "fault tolerant", "fault tolerance",
                     "mission critical", "enterprise grade", "production grade", "battle tested", "world class"),
          support_phrases=_p("high availability", "highly available", "zero downtime", "fault tolerant",
                             "fault tolerance", "mission critical", "enterprise grade", "production grade",
                             "battle tested", "world class")),
    # invented scope: impact
    Claim("scope", "outperforming", STRONG, triggers=_f("outperformed outperforming outperforms outperform surpassed "
                                                         "surpassing surpasses sota"),
          phrases=_p("state of the art", "best in class"),
          support=_f("outperformed outperforming outperforms outperform surpassed surpassing surpasses sota beat beats "
                     "beating exceeded exceeds better improved improving improves improvement gain gained higher "
                     "lifted lifting raised raising boosted outpaced"),
          support_phrases=_p("state of the art", "best in class"), exact=True),
    Claim("scope", "eliminating", STRONG, triggers=_f("eliminated eliminating eliminates eliminate eradicated"),
          support=_f("eliminated eliminating eliminates eliminate eradicated removed removing remove removes deleted "
                     "dropped fixed fixes zero automated automate automating automates replaced replace replacing replaces"),
          exact=True),
    Claim("scope", "vague impact", SOFT,
          triggers=_f("significant significantly substantial substantially dramatic dramatically massive massively "
                      "tremendous drastically drastic huge"),
          support=_f("significant significantly substantial substantially dramatic dramatically massive massively "
                     "tremendous drastically drastic huge"), exact=True, skip_prev=_f("statistically")),
    # a deployment or a replacement the evidence never describes (each soft: two together are a claim)
    Claim("scope", "deployed", SOFT, triggers=_f("deployed deploys deploying"), support=_DEPLOY_OK),
    Claim("scope", "replaced", SOFT, triggers=_f("replaced replaces replacing"), support=_REPLACE_OK,
          phrases=(), support_phrases=_p("instead of", "rather than")),
)

# Named methods and model families. A specific name is a claim the generic word does not make
# ("clustering" does not say k-means), and a framework implies the family it is built for.
_DEEP = _f("""pytorch torch tensorflow keras jax neural cnn rnn lstm gru transformer transformers bert gpt convolutional
deep mxnet onnx tensorrt huggingface diffusion gan autoencoder""")
_METHODS: Tuple[Tuple[str, Tuple[str, ...], FrozenSet[str]], ...] = (
    ("deep learning", _p("deep learning", "neural network", "neural networks"), _DEEP),
    ("k-means", _p("k means", "kmeans"), _f("kmeans")),
    ("random forest", _p("random forest", "random forests"), _f("forest forests")),
    ("gradient boosting", _p("gradient boosting", "gradient boosted"), _f("xgboost lightgbm catboost gbm boosting boosted")),
    ("reinforcement learning", _p("reinforcement learning"), _f("reinforcement rl bandit bandits")),
    ("regression", _p("logistic regression", "linear regression"), _f("regression regress glm")),
    ("svm", _p("support vector", "svm"), _f("svm svc")),
)

_MAGNITUDE = {"dozens": 24.0, "hundreds": 100.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
_MAGNITUDE_OK = {"dozens": _f("dozens dozen"), "hundreds": _f("hundreds"), "thousands": _f("thousands"),
                 "millions": _f("millions"), "billions": _f("billions")}


# ── contradiction vocabulary ─────────────────────────────────────────────────

_NEGATOR = _f("without no never nor")
_NEGATOR_PHRASES = _p("instead of", "rather than", "as opposed to")
_BULLET_NEGATION = _f("""without no never not non free less fewer untouched unchanged avoid avoids avoiding avoided
prevent prevents preventing eliminate eliminated eliminating eliminates reduce reduced reducing reduces cut
cutting lowered lowering minimize minimized zero nothing none unaided""") | _f("instead rather")
_DETERMINER = _f("a an the any its their our my his her this that these those to being of using needing having "
                 "requiring relying")

_ANTONYMS: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...] = (
    # (name, A phrases, A masks, B phrases) : A and B cannot both describe the same work
    ("where it runs",
     _p("offline", "locally", "local first", "on device", "air gapped"),
     _p("without a network call", "without network", "without internet", "no network", "no internet",
        "without a network", "without any network"),
     _p("remote", "hosted", "in the cloud", "cloud hosted", "online", "api call", "network call", "over the network")),
    ("significance",
     _p("statistically flat", "statistically insignificant", "insignificant", "inconclusive", "not significant",
        "no significant", "not statistically significant", "no measurable effect"),
     _p("not significant", "no significant", "not statistically significant"),
     _p("statistically significant", "significant winner", "significant winners", "winner", "winners")),
)
_MODALITY = {"image": _f("image images"), "text": _f("text document documents"), "audio": _f("audio speech voice"),
             "video": _f("video videos"), "tabular": _f("tabular")}
_MODALITY_TASK = _f("""classification classifier classifiers recognition detection segmentation generation model models
dataset datasets data backbone backbones embedding embeddings retrieval search analysis corpus""")

_DOWN = _f("reduced reducing reduces cut cutting decreased decreasing decreases lowered lowering shortened shrank slashed minimized")
_UP = _f("increased increasing increases raised raising raises boosted boosting grew expanded lengthened maximized")
_OBJECT_END = _f("by from to in on with across over through using via for at of per into than and or but while so")
_ATTRIBUTION = re.compile(
    r"\b(?:written|built|created|developed|designed|authored|implemented|made|maintained|owned|run|led) by "
    r"(?:another|someone else|others|other engineers?|a colleague|a coworker|a teammate|teammates|an earlier|a previous|the previous)\b")
_FROM_TO = re.compile(r"\bfrom ([^,;.]+?) (?:back )?to ([^,;.]+?)(?=[,;.]|$| with | using | in | for | across | and | so | by | on )")


def _words(text: str) -> FrozenSet[str]:
    return frozenset(t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 1)


# ── the checks ───────────────────────────────────────────────────────────────

def _trigger_hits(c: Claim, b: _Text) -> List[str]:
    """The words or phrases of `c` the bullet uses, in order."""
    out: List[str] = []
    toks = b.tokens
    for i, t in enumerate(toks):
        if t not in c.triggers:
            continue
        if i + 1 < len(toks) and toks[i + 1] in c.skip_next:
            continue
        if i and toks[i - 1] in c.skip_prev:
            continue
        out.append(t)
    out += [p for p in c.phrases if b.phrase(p)]
    return out


def _supported(c: Claim, ev: _Text) -> bool:
    if c.exact:
        if ev.set & c.support:
            return True
    else:
        if ev.stems & {_stem(w) for w in c.support}:
            return True
    return ev.any_phrase(c.support_phrases)


_LEAD_NOUN = _f("generation gen scoring time times source sources engineer developer scientist analyst designer magnet "
                "nurture capture conversion qualification poisoning paint")


def _lead_verb(b: _Text) -> Optional[str]:
    """`lead` / `leads` read as a verb: first word of the bullet, or right after "and" / "to" / "will",
    and not the front of a noun phrase ("lead generation", "lead engineer")."""
    for i, t in enumerate(b.tokens):
        if t in ("lead", "leads") and (i == 0 or b.tokens[i - 1] in ("and", "to", "will", "also")):
            if i + 1 < len(b.tokens) and b.tokens[i + 1] in _LEAD_NOUN:
                continue
            return t
    return None


def _claim_signals(b: _Text, ev: _Text, ctx: _Text, orig: _Text, junior: bool) -> List[Signal]:
    out: List[Signal] = []
    for c in _CLAIMS:
        hits = _trigger_hits(c, b)
        if c.concept == "authority":
            lead = _lead_verb(b)
            if lead:
                hits.append(lead)
        if not hits or _supported(c, ctx):
            continue
        weight = STRONG if (junior and c.kind == "role") else c.weight
        # the revision did not add it: the original bullet already said it
        if all(h in orig.set or orig.phrase(h) for h in hits):
            weight = weight * HALF
        if weight:
            out.append(Signal(c.kind, "/".join(sorted(set(hits))), weight))
    return out


def _method_signals(b: _Text, ctx: _Text, orig: _Text) -> List[Signal]:
    out = []
    for name, phrases, support in _METHODS:
        if not b.any_phrase(phrases) or ctx.any_phrase(phrases) or (ctx.folded & support):
            continue
        out.append(Signal("method", name, STRONG * (HALF if orig.any_phrase(phrases) else 1.0)))
    return out


def _quantity_signals(b: _Text, ev: _Text, ctx: _Text) -> List[Signal]:
    out = []
    nums = _numbers(ctx.raw)
    for word, bound in _MAGNITUDE.items():
        if word not in b.set or ctx.set & _MAGNITUDE_OK[word] or any(n >= bound for n in nums):
            continue
        out.append(Signal("quantity", word, STRONG if nums else SOFT, CONTRADICTS if nums else ADDS))
    return out


# The skill alias map is the vocabulary of tools the knowledge graph knows. A tool in it that the
# bullet names and the evidence does not (folded, so "torch" in the evidence covers "PyTorch" in the
# bullet) is a new named thing. #123 reads capitalised names and its own technology list; this reads the
# lowercase and aliased spellings of the graph's own tools. The bare languages and markup stay out
# (python, java, sql, html, css, r): a library implies its language, and "Postgres" implies "SQL".
_LANGUAGES = _f("python java sql html css r javascript typescript")
_TOOL_FORMS: Dict[str, str] = {}
for _alias, _canon in SKILL_ALIASES.items():
    for _form in (_alias, _canon):
        _key = _norm_phrase(_form)
        if _key and _key not in _LANGUAGES and len(_key) > 2:
            _TOOL_FORMS[_key] = _canon.lower()


def _tool_signals(b: _Text, ctx: _Text, orig: _Text) -> List[Signal]:
    out, seen = [], set()
    for form, canon in sorted(_TOOL_FORMS.items()):
        if canon in seen or not b.phrase(form):
            continue
        covered = canon in ctx.folded or any(ctx.phrase(f) for f, c in _TOOL_FORMS.items() if c == canon)
        if covered:
            continue
        seen.add(canon)
        out.append(Signal("tool", canon, STRONG * (HALF if orig.phrase(form) else 1.0)))
    return out


def _negated_heads(ev: _Text) -> List[str]:
    """The word each negation in the evidence scopes over: "without rewriting a draft" -> rewriting."""
    toks, heads = ev.tokens, []
    for i, t in enumerate(toks):
        j = None
        if t in _NEGATOR:
            j = i + 1
        else:
            for p in _NEGATOR_PHRASES:
                n = len(p.split())
                if " ".join(toks[i:i + n]) == p:
                    j = i + n
                    break
        if j is None:
            continue
        while j < len(toks) and toks[j] in _DETERMINER:
            j += 1
        # only an action ("without rewriting"); a noun after "no" or "without" ("a network call")
        # also names a thing the bullet may mention for another reason
        if j < len(toks) and toks[j].endswith("ing") and len(toks[j]) >= 6 and toks[j] not in _STOP:
            heads.append(toks[j])
    return heads


def _polarity(b: _Text, ev: _Text) -> List[Signal]:
    """The evidence says "without X" or "instead of X" and the bullet does X, with no negation of
    its own for the reader to weigh. Silent when the bullet has any negating word at all."""
    if b.set & _BULLET_NEGATION or b.any_phrase(_NEGATOR_PHRASES):
        return []
    out = []
    for head in _negated_heads(ev):
        if _stem(head) in b.stems and _stem(head) not in {_stem(t) for t in _non_negated(ev)}:
            out.append(Signal("contradiction", f"drops the negation of {head}", STRONG, CONTRADICTS))
    return out


def _non_negated(ev: _Text) -> List[str]:
    """Evidence tokens outside a negation's scope (so a head that the evidence also uses plainly
    elsewhere is not read as a flip)."""
    toks, skip, keep = ev.tokens, set(), []
    for i, t in enumerate(toks):
        if t in _NEGATOR or t in ("instead", "rather", "opposed"):
            skip.update(range(i, min(i + 4, len(toks))))
    for i, t in enumerate(toks):
        if i not in skip:
            keep.append(t)
    return keep


def _mask(text: _Text, phrases: Sequence[str]) -> str:
    norm = text.norm
    for p in sorted(phrases, key=len, reverse=True):
        norm = norm.replace(f" {p} ", " __ ")
    return norm


def _antonyms(b: _Text, ev: _Text, ctx: _Text) -> List[Signal]:
    out = []
    for name, a_phr, a_mask, b_phr in _ANTONYMS:
        ev_norm = _mask(ev, a_mask)
        ev_a = any(f" {p} " in ev_norm for p in a_phr) or any(ev.phrase(m) for m in a_mask)
        b_norm_masked = _mask(b, a_mask)
        bullet_a = any(f" {p} " in b_norm_masked for p in a_phr) or any(b.phrase(m) for m in a_mask)
        bullet_b = any(f" {p} " in b_norm_masked for p in b_phr)
        ctx_norm = _mask(ctx, a_mask)
        ctx_b = any(f" {p} " in ctx_norm for p in b_phr)
        if ev_a and bullet_b and not bullet_a and not ctx_b:
            out.append(Signal("contradiction", f"{name}: the evidence says the opposite", STRONG, CONTRADICTS))
    # an image model turned into a text one
    def modes(t: _Text) -> Set[str]:
        got = set()
        for i, tok in enumerate(t.tokens[:-1]):
            if t.tokens[i + 1] in _MODALITY_TASK:
                got |= {m for m, words in _MODALITY.items() if tok in words}
        return got
    em, bm = modes(ev), modes(b)
    if em and bm and not (em & bm) and not (modes(ctx) & bm):
        out.append(Signal("contradiction", f"{'/'.join(sorted(bm))} where the evidence has {'/'.join(sorted(em))}",
                          STRONG, CONTRADICTS))
    return out


def _direction(b: _Text, ev: _Text) -> List[Signal]:
    """A verb that reverses the evidence's on the same object ("cut token spend" -> "increased token
    spend"), and a migration read backwards ("from cron to Airflow" -> "from Airflow back to cron")."""
    out = []

    def objects(t: _Text, verbs: FrozenSet[str]) -> Set[str]:
        """The head noun of what each verb acts on: the last content word before the next
        preposition or number ("cut token spend by 40%" -> spend). Two bullets share an object only
        when their heads are the same word, so "raised ranking precision" and "cut ranking latency"
        do not collide on "ranking"."""
        got: Set[str] = set()
        for i, tok in enumerate(t.tokens):
            if tok not in verbs:
                continue
            head = None
            for w in t.tokens[i + 1:i + 7]:
                if w in _OBJECT_END or w.isdigit():
                    break
                if w not in _STOP and len(w) > 2:
                    head = _stem(w)
            if head:
                got.add(head)
        return got

    for ev_verbs, b_verbs in ((_DOWN, _UP), (_UP, _DOWN)):
        if (ev.set & ev_verbs) and not (ev.set & b_verbs) and (b.set & b_verbs) and not (b.set & ev_verbs):
            if objects(ev, ev_verbs) & objects(b, b_verbs):
                out.append(Signal("contradiction", "reverses the direction of a change", STRONG, CONTRADICTS))
                break

    def pairs(t: _Text) -> List[Tuple[FrozenSet[str], FrozenSet[str]]]:
        res = []
        for m in _FROM_TO.finditer(t.low):
            x, y = _words(m.group(1)) | frozenset(_TOKEN.findall(m.group(1))), _words(m.group(2)) | frozenset(_TOKEN.findall(m.group(2)))
            if x and y:
                res.append((frozenset(_stem(w) for w in x), frozenset(_stem(w) for w in y)))
        return res

    ep, bp = pairs(ev), pairs(b)
    for ex, ey in ep:
        for bx, by in bp:
            if bx == ey and by == ex and ex != ey:
                out.append(Signal("contradiction", "reads a from-to change backwards", STRONG, CONTRADICTS))
                return out
    return out


def _attribution(b: _Text, ev: _Text) -> List[Signal]:
    if _ATTRIBUTION.search(b.low) and not _ATTRIBUTION.search(ev.low):
        return [Signal("contradiction", "credits the work to someone else", STRONG, CONTRADICTS)]
    return []


# ── the check ────────────────────────────────────────────────────────────────

def assess(bullet: str, evidence: Sequence[str], original: Optional[str] = None,
           context: Sequence[str] = ()) -> Dict[str, Any]:
    """What `bullet` adds to its cited `evidence`.

    `context` is what a real item carries besides the cited bullets (its other source bullets and
    its header): it can only make a claim supported, never contradict, so a claim the item makes
    elsewhere is not flagged. Returns `{score, label, signals}`; `label` is `contradicts` when a
    contradiction signal fired, `adds_unsupported` for any other score above 0, else `supported`.
    Pure and deterministic."""
    ev = _Text(" . ".join(e for e in evidence if e))
    ctx = _Text(" . ".join([*(e for e in evidence if e), *(c for c in context if c)]))
    b, orig = _Text(bullet), _Text(original or "")
    junior = bool(ev.set & _HELPER) and not (ev.set & _AUTHORITY_OK)
    signals: List[Signal] = []
    signals += _claim_signals(b, ev, ctx, orig, junior)
    signals += _method_signals(b, ctx, orig)
    signals += _quantity_signals(b, ev, ctx)
    signals += _tool_signals(b, ctx, orig)
    signals += _polarity(b, ev)
    signals += _antonyms(b, ev, ctx)
    signals += _direction(b, ev)
    signals += _attribution(b, ev)
    signals.sort(key=lambda s: (-s.weight, s.kind, s.claim))
    score = round(sum(s.weight for s in signals), 2)
    label = CONTRADICTS if any(s.label == CONTRADICTS for s in signals) else (ADDS if signals else "supported")
    return {"score": score, "label": label, "signals": signals}


def finding(item: str, bullet: str, state: Dict[str, Any], context: Sequence[str] = (),
            reason: Optional[str] = None) -> Dict[str, Any]:
    """The support finding the fallback gives a bullet Jev did not answer, in the shape
    `support.violations` / `support.reviews` read (`status: "heuristic"`)."""
    a = assess(state["bullet"], state["evidence"], state.get("original"), context)
    return {"item": item, "bullet": bullet, "status": "heuristic", "label": a["label"], "p": None,
            "probabilities": None, "score": a["score"], "signals": [s.text() for s in a["signals"]],
            "source": "fallback", "reason": reason, "model": MODEL}
