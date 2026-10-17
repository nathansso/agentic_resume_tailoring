"""What kind of work a text describes, read with a small fixed vocabulary (issue #352). No model.

The Jev-free fallbacks for two library decisions (`harness/library.py`) need the same thing: a
way to say that "trained a scikit-learn churn classifier, AUC 0.71 to 0.83" and "build predictive
models using statistics and machine learning" are the same kind of work although they share no
word, and that a bullet about terraform and a bullet about grading are not. Word overlap cannot
(`relevance_density` was 18/48 on `eval/library_labels/`, barely above answering `no_match` every
time), so the text is first reduced to the *kinds of work* it names.

- `CONCEPTS` is the vocabulary: a few dozen kinds of work, each a list of word patterns.
  A pattern is matched on whole words (a trailing `\\w*` takes any ending), so "pipeline" is a
  data-engineering word and "pipelines" is too. Skill aliases are folded in the patterns
  (`sklearn`, `scikit-learn`; `torch`, `pytorch`; `postgres`, `postgresql`).
- `read(text)` is `{concept: hits}`, hits being the number of distinct patterns that matched,
  capped at `HIT_CAP` so one repeated stack does not outweigh the rest.
- `read_job(title, requirements)` is the job's reading: each concept's share of the top
  requirements, most critical counting most, and the title as one more requirement of weight
  `TITLE_WEIGHT`.
- `demand(job, text_reading)` is the variant score: the average, over the kinds of work a bullet
  shows, of the share of the job's requirements that ask for that kind. A bullet about work the
  posting does not ask for scores near 0 whatever nouns it repeats, and `mostly_unasked` zeroes a
  bullet that is mostly teaching, office work or sales against a technical posting: the
  keyword-stuffed case.
- `affinity(a, b)` is the cosine of two readings and `FAMILIES` reads each role family as a profile
  over the same concepts, so a saved track is scored on the job's duties and not on its title alone.
  `OFF_TRACK` adds kinds of work no track covers, so a job that is mostly one of them is not handed
  to the track it shares a few words with.

Everything is deterministic and stdlib-only. The vocabulary is written by hand from what the role
families do; it is not fitted. Only the floors and margins that use it are fitted
(`eval/fit_library_fallbacks.py`, `harness/policy.json`).
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

HIT_CAP = 2                 # distinct patterns of one concept that count in a text
TITLE_WEIGHT = 3            # the job title counts as one requirement of this criticality
MIN_WEIGHT = 12.0           # shares are out of at least this total, so a title alone (3) is thin evidence
                            # (three requirements of criticality 3 to 5 and a title already reach it)
MAX_REQUIREMENTS = 8        # the requirements read, most critical first (the count Jev is shown)

# concept -> word patterns (lowercase regex fragments). Order is irrelevant.
CONCEPTS: Dict[str, Tuple[str, ...]] = {
    # fitting models to data to predict something
    "modeling": (
        r"classif\w*", r"regress\w*", r"predict\w*", r"forecast\w*", r"scikit-?learn|sklearn",
        r"xgboost|lightgbm|statsmodels", r"supervised|unsupervised", r"feature engineering",
        r"machine learning|ml", r"auc", r"held-?out", r"top-\d+ accuracy",
        r"(?:predictive|statistical|ranking|recommendation|demand|churn|training)\s+models?",
        r"train\w*\s+(?:\w+\s+){0,3}?(?:models?|classifiers?|networks?|nets?)\b",
        r"recommend\w*", r"anomal\w*|outliers?|unusual", r"ranking",
    ),
    # neural networks and the tooling around them
    "deeplearning": (
        r"deep learning", r"neural", r"cnns?", r"convolution\w*", r"resnet\w*", r"autoencoder\w*",
        r"transformers?", r"pytorch|torch", r"tensorflow|keras", r"gpus?", r"embeddings?",
        r"fine-?tun\w*", r"llms?|language models?", r"nlp|natural language", r"computer vision",
        r"image (?:recognition|classification)", r"augmentation", r"inference", r"mixup",
    ),
    # putting models in front of users and watching them
    "mlops": (
        r"model (?:serving|monitoring|deployment)", r"deploy\w* (?:\w+ )?models?", r"drift",
        r"serv\w* (?:predictions|models)", r"edge devices?", r"feature stores?", r"mlflow",
        r"models? (?:to|in) production", r"sagemaker",
    ),
    # drawing conclusions with uncertainty
    "stats": (
        r"statistic\w*", r"hypothes\w*", r"a/b|ab tests?|price tests?|online experiments?",
        r"experiment\w*", r"confidence intervals?", r"bootstrap\w*", r"causal", r"significan\w*",
        r"survival", r"hazards?", r"cox", r"bayes\w*", r"probabilit\w*", r"elasticit\w*",
        r"mixed-?effects?", r"clustering", r"econometric\w*", r"variance", r"clinical",
        r"missing data", r"uncertainty",
    ),
    # reporting what the data says to people who decide
    "analytics": (
        r"dashboards?", r"reports?|reporting", r"visuali[sz]\w*", r"tableau|power bi|looker",
        r"kpis?", r"metrics?", r"insights?", r"stakeholders?", r"merchants?", r"cohorts?",
        r"matplotlib|seaborn|plotly", r"excel", r"trends?", r"analys[ie]s|analy[sz]e\w*|analytics|analyst",
        r"business (?:questions|problems)", r"margin", r"segment\w*",
    ),
    # explaining results to non-specialists
    "communication": (
        r"present\w*", r"explain\w*", r"communicat\w*", r"findings", r"leadership", r"clinicians?",
        r"managers", r"customers?", r"non-technical",
    ),
    # SQL and relational data access (shared by several roles)
    "sql": (r"sql", r"queries|query", r"relational", r"postgres\w*|mysql|sqlite"),
    # moving and shaping data in bulk
    "pipelines": (
        r"pipelines?", r"etl|elt", r"warehous\w*", r"airflow|dags?", r"dbt", r"spark|pyspark",
        r"kafka|flink|kinesis", r"snowflake|bigquery|redshift|databricks",
        r"orchestrat\w*", r"ingest\w*", r"batch", r"stream(?:s|ing)?", r"data lakes?",
        r"hadoop|scala", r"data (?:quality|contracts?|platform|assets?)", r"dedup\w*|de-?duplicat\w*",
        r"backfill\w*", r"schedul\w*|cron", r"freshness", r"lineage",
    ),
    # services other software calls
    "backend": (
        r"apis?", r"rest|restful", r"grpc", r"graphql", r"microservices?", r"back-?end",
        r"server-side", r"services?", r"endpoints?", r"flask|django|fastapi|spring",
        r"idempoten\w*", r"distributed", r"concurren\w*", r"on-call", r"redis|queues?",
        r"golang|java|kotlin", r"payments?", r"schemas?", r"migrations?", r"p95|p99", r"latency",
        r"retries|retry", r"transactions?", r"reliab\w*",
    ),
    # what runs in a browser
    "frontend": (
        r"react|vue|angular|svelte", r"next\.?js", r"html", r"css", r"javascript|typescript",
        r"ui|user interfaces?", r"front-?end", r"responsive", r"accessib\w*", r"components?",
        r"web (?:app|apps|application|applications|site)s?", r"lighthouse|web vitals",
        r"page load", r"browsers?", r"keyboard navigation", r"lazy-?load\w*", r"portfolio site",
    ),
    # running things reliably
    "infra": (
        r"docker\w*|containeri[sz]\w*|containers?", r"kubernetes|k8s|helm", r"terraform|ansible",
        r"ci/cd|cicd", r"deploy\w*", r"aws|gcp|azure|cloud", r"provision\w*", r"infrastructure",
        r"uptime", r"monitor\w*", r"grafana|prometheus|observab\w*", r"linux", r"clusters?",
        r"alert\w*", r"environments?",
    ),
    # checking that software works
    "testing": (
        r"tests?|testing|tested", r"cypress|pytest|jest|e2e|end-to-end", r"unit", r"autograder\w*",
        r"coverage", r"code review|review code",
    ),
    "hardware": (
        r"hardware", r"asics?", r"fpgas?", r"rtl", r"verilog|systemverilog|vhdl|uvm", r"timing", r"silicon",
        r"circuits?|pcb", r"firmware|embedded", r"tcl", r"place and route|clock tree|floorplan\w*",
        r"synopsys|cadence", r"sub-?micron|wafer", r"testbench\w*", r"electrical", r"waveforms?",
    ),
    "security": (
        r"security", r"penetration|pentest\w*", r"threats?", r"siem", r"incidents?", r"vulnerab\w*",
        r"firewalls?", r"encrypt\w*", r"authentication", r"tls", r"malware", r"detection rules?",
    ),
    # work with students and learners
    "teaching": (
        r"teach\w*|taught", r"tutor\w*", r"grad(?:e|ed|es|ing)", r"lab sections?", r"students?",
        r"courses?|curricul\w*", r"lectur\w*", r"assignments?", r"office hours", r"workshops?",
        r"feedback on",
    ),
    # running a team or an office
    "admin": (
        r"stand-?ups?", r"retrospectives?", r"sprints?", r"onboarding", r"wiki", r"newsletter",
        r"symposium", r"poster", r"facilitat\w*", r"cashiers?", r"point-of-sale", r"figures",
        r"annual report", r"agile",
    ),
    "writing": (
        r"documentation", r"technical writ\w*", r"release notes", r"tutorials?", r"edit\w*",
        r"written english", r"knowledge base",
    ),
    "sales": (
        r"sales?", r"quota", r"crm", r"deals?", r"prospect\w*", r"demos?", r"proofs? of concept",
        r"negotiat\w*", r"account executives?", r"campaigns?", r"marketing (?:manager|team|campaigns?)",
        r"brand voice", r"content calendar", r"paid channels",
    ),
    "support": (
        r"tickets?", r"help ?desk", r"set up laptops?", r"user accounts?", r"customer service", r"hardware and software",
        r"comptia", r"manual (?:test|testing|test plans)", r"defects?", r"bugs?", r"data entry",
        r"enter \w+ records",
    ),
    "finance": (
        r"financ\w*", r"budgets?", r"forecast revenue", r"variance", r"accounting", r"spending",
        r"revenue", r"planning cycle",
    ),
    "people": (r"recruit\w*", r"candidates?", r"hiring", r"interview\w*", r"applicant tracking"),
    "product": (
        r"roadmap", r"product requirements?", r"customer interviews?", r"prioriti[sz]e",
        r"product manager", r"success metrics",
    ),
    "design": (
        r"wireframes?", r"prototypes?", r"figma", r"usability", r"user flows?", r"design system",
        r"graphic", r"ux",
    ),
    "governance": (
        r"governance", r"catalog", r"glossary", r"compliance", r"privacy", r"stewardship", r"audits?",
        r"regulat\w*",
    ),
    "annotation": (r"label\w*", r"annotat\w*"),
    "research": (
        r"research\w*", r"papers?", r"literature", r"publications?", r"publish\w*", r"reproduce",
    ),
    "dba": (r"backups?", r"replication", r"failover", r"patches", r"user access", r"indexes|indices"),
}

_COMPILED: Dict[str, List[re.Pattern]] = {
    name: [re.compile(rf"(?<![a-z0-9+#]){p}(?![a-z0-9+#])") for p in pats]
    for name, pats in CONCEPTS.items()
}
_CASE_SENSITIVE_GO = re.compile(r"(?<![A-Za-z0-9])Go(?![A-Za-z0-9])")


def read(text: Any) -> Dict[str, int]:
    """`{concept: hits}` for the concepts `text` names, hits capped at `HIT_CAP`."""
    s = " ".join(str(text or "").split())
    low = re.sub(r"\ba/b[- ]test\w*", "a/b", s.lower())     # an A/B test is statistics, not software testing
    out: Dict[str, int] = {}
    for name, pats in _COMPILED.items():
        hits = sum(1 for p in pats if p.search(low))
        if name == "backend" and _CASE_SENSITIVE_GO.search(s):
            hits += 1           # the language, never the verb: only its own capitalization counts
        if hits:
            out[name] = min(hits, HIT_CAP)
    return out


# ── a job, and a role family, as readings ────────────────────────────────────

def requirement_rows(requirements: Iterable[Mapping]) -> List[Tuple[float, str]]:
    """`(criticality, text)` of the required and preferred requirements, most critical first, then in
    posting order, at most `MAX_REQUIREMENTS`. Incidental ones take no part, as in `coverage.py`."""
    rows: List[Tuple[float, str]] = []
    for r in requirements or []:
        if not isinstance(r, Mapping) or str(r.get("type") or "required") not in ("required", "preferred"):
            continue
        text = " ".join(str(r.get("text") or "").split())
        if text:
            try:
                crit = float(r.get("criticality") or 3)
            except (TypeError, ValueError):
                crit = 3.0
            rows.append((max(crit, 1.0), text))
    ranked = sorted(enumerate(rows), key=lambda ir: (-ir[1][0], ir[0]))
    return [r for _, r in ranked][:MAX_REQUIREMENTS]


def read_job(title: str, requirements: Iterable[Mapping]) -> Dict[str, float]:
    """The job's reading: for each concept, its share of the top requirements' weight (a requirement
    names it with `hits / HIT_CAP` of its criticality), most critical first, the title as one more
    requirement of weight `TITLE_WEIGHT`. Values are in [0, 1]; a job the vocabulary reads thinly has
    small ones. Empty when there is nothing to read."""
    rows = requirement_rows(requirements)
    if title and title.strip():
        rows.append((float(TITLE_WEIGHT), title))
    total = max(sum(w for w, _ in rows), MIN_WEIGHT)
    reading: Dict[str, float] = {}
    if not rows:
        return reading
    for weight, text in rows:
        for concept, hits in read(text).items():
            reading[concept] = reading.get(concept, 0.0) + weight * hits / HIT_CAP / total
    return reading


def _norm(v: Mapping[str, float]) -> float:
    return math.sqrt(sum(x * x for x in v.values()))


def demand(job: Mapping[str, float], text_reading: Mapping[str, float]) -> float:
    """How much of what a text shows the job asks for: the average, over the kinds of work the text
    names (each by its hits), of that kind's share of the job's top requirements. 0 when the job
    asks for none of it (a grading bullet against a software posting) or when the text names nothing;
    a job the vocabulary barely reads gives every text a low value."""
    total = sum(text_reading.values())
    if total <= 0:
        return 0.0
    return sum(h * job.get(c, 0.0) for c, h in text_reading.items()) / total


# Kinds of work that are not technical duties. A bullet that is mostly about one of them, when the
# posting does not ask for it, is not a demonstration of the posting's work, however many of the
# posting's nouns it repeats ("Graded 90 REST API and microservices assignments").
OFF_DUTY = frozenset({"teaching", "admin", "support", "people", "writing", "sales", "finance",
                      "governance", "annotation"})
UNASKED = 0.05              # the posting asks for a kind of work when it holds at least this share
UNASKED_HITS = 2            # a text is mostly about unasked work with at least this many hits on it ...
UNASKED_SHARE = 0.4         # ... and at least this share of all its hits


def mostly_unasked(text_reading: Mapping[str, float], job: Mapping[str, float]) -> bool:
    """Whether the text is mostly about work the posting does not ask for (`OFF_DUTY` kinds below
    `UNASKED` in the job's reading)."""
    total = sum(text_reading.values())
    off = sum(h for c, h in text_reading.items() if c in OFF_DUTY and job.get(c, 0.0) < UNASKED)
    return total > 0 and off >= UNASKED_HITS and off >= UNASKED_SHARE * total


def affinity(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Cosine of two readings: 1 for the same kinds of work in the same proportions, 0 for none shared."""
    na, nb = _norm(a), _norm(b)
    if na <= 0 or nb <= 0:
        return 0.0
    return sum(x * b.get(k, 0.0) for k, x in a.items()) / (na * nb)


# What each role family does, as weights over the concepts. Written from the family's definition,
# not fitted. Families a user can save a track for are all here; a track with another name reads as
# the family of the job it came from, else is never picked by the fallback.
FAMILIES: Dict[str, Dict[str, float]] = {
    "data_science": {"stats": 3, "analytics": 3, "modeling": 2, "communication": 1, "sql": 1},
    "machine_learning": {"modeling": 3, "deeplearning": 3, "mlops": 2, "stats": 0.5, "pipelines": 0.5},
    "data_engineering": {"pipelines": 4, "sql": 1, "infra": 0.5},
    "software_engineering": {"backend": 3, "frontend": 3, "testing": 1.5, "infra": 1, "sql": 0.5},
    "devops_infrastructure": {"infra": 4, "backend": 0.5, "testing": 0.5},
    "security": {"security": 4, "infra": 0.5},
    "hardware": {"hardware": 4},
    "design": {"design": 3, "frontend": 0.5},
    "product_management": {"product": 3, "analytics": 0.5, "communication": 0.5},
    "research": {"research": 3, "modeling": 1, "stats": 1},
}

# Kinds of work that no saved track covers. They are scored beside the tracks so that a job which is
# mostly one of them (support, sales, writing, governance) is not handed to the track it shares a few
# words with.
OFF_TRACK: Dict[str, Dict[str, float]] = {
    "writing": {"writing": 3}, "sales": {"sales": 3}, "support": {"support": 3},
    "finance": {"finance": 3, "analytics": 0.5}, "people": {"people": 3},
    "governance": {"governance": 3}, "annotation": {"annotation": 3}, "admin": {"admin": 3},
    "dba": {"dba": 3, "sql": 1}, "teaching": {"teaching": 3},
}


def family_scores(job: Mapping[str, float], families: Optional[Iterable[str]] = None) -> Dict[str, float]:
    """Affinity of the job's reading with each family's profile, for `families` (default: every
    family and every off-track kind of work)."""
    profiles = {**FAMILIES, **OFF_TRACK}
    names = list(profiles) if families is None else [f for f in families if f in profiles]
    return {f: affinity(job, profiles[f]) for f in names}
