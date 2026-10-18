"""Skill areas for the evidence map (issue #136, #318).

`Skill.category` is free text ("Programming Language", "Languages & Libraries",
"Technique", ...), far too many values to group a skills column by. This maps a
skill to one of a fixed, small set of areas, in two steps:

1. **The keyword table** (`keyword_area`), a pure function of `(name, category)`: no
   model call, no storage, the same answer on every request. Most specific first:
   the skill's name against explicit name tables (a language is a language whatever
   category an ingest gave it); the curated name -> bucket map in
   `agents.skill_postprocessor`; the free-text category, tokenized so "ai" does not
   match "email".
2. **Cached Jev** (#318), only for a skill the table leaves in "Other".
   `harness/decisions/skill_area.py` asks once per skill, when the skill is written
   (`KGStoreMixin._save_skills`) or by `classify_unplaced_skills` for a profile that
   already exists, and the answer lives in the Jev decision cache. `skill_area` and
   `skill_areas` only *read* that cache: they never make a call, so a request that
   lists a profile's skills stays offline and fast, and a skill nobody has classified
   yet reads as "Other".

Callers only ever see `skill_area` / `skill_areas`. A misfiled skill is fixed here (a
name table) or in the Jev label set (`AREA_DESCRIPTIONS`, with a version bump), never
per user.
"""
import re
from typing import Dict, Iterable, Optional, Tuple

from agents.skill_postprocessor import (
    AIML, CLOUD, DATA, LANGS, SKILL_CATEGORIES, TOOLS, normalize_skill_name,
)

LANGUAGES = "Languages"
ML_AI = "ML & AI"
DATA_AREA = "Data"
INFRA = "Infrastructure & Cloud"
WEB = "Web & Apps"
TOOLS_AREA = "Tools"
PRACTICES = "Practices"
OTHER = "Other"

# The fixed set, in the order the UI falls back to for ties.
AREAS = (LANGUAGES, ML_AI, DATA_AREA, INFRA, WEB, TOOLS_AREA, PRACTICES, OTHER)

# What each area holds, in words: the option descriptions of the Jev question (#318).
# Rewording one means bumping `harness.decisions.skill_area.VERSION`.
AREA_DESCRIPTIONS = {
    LANGUAGES: "Programming, query and markup languages, such as Python, SQL, C++ or HTML.",
    ML_AI: "Machine learning, deep learning, NLP, LLMs and the libraries and methods behind them.",
    DATA_AREA: "Data analysis, statistics, databases, ETL, data libraries and visualization.",
    INFRA: "Cloud platforms, deployment, containers, networking, DevOps and hosting.",
    WEB: "Web and mobile development: frameworks, front end, back end and APIs.",
    TOOLS_AREA: "Software tools and platforms used day to day, such as editors, design and "
                "project tools, version control or office software.",
    PRACTICES: "Methods and working practices: testing, agile, system design and soft skills "
               "such as communication or leadership.",
}

_LANGUAGE_NAMES = frozenset((
    "python", "sql", "r", "java", "javascript", "typescript", "c", "c++", "c#", "go",
    "golang", "rust", "scala", "kotlin", "swift", "php", "ruby", "matlab", "bash",
    "shell", "perl", "julia", "haskell", "dart", "lua", "objective-c", "r language",
    "sas", "solidity", "elixir", "clojure", "assembly", "verilog", "vhdl", "html", "css",
))

_WEB_NAMES = frozenset((
    "react", "react native", "node.js", "next.js", "vue", "angular", "svelte", "tailwind css",
    "jquery", "flask", "django", "fastapi", "spring boot", "express", "express.js",
    "graphql", "rest", "rest api", "rest apis", "sqlalchemy", "sqlmodel", "pydantic",
    "flutter", "android", "ios", "swiftui", "vite", "webpack", "redux", "html5", "css3",
    "frontend", "backend", "full-stack", "full stack", "websockets", "astro", "electron",
))

_DATA_SCIENCE_NAMES = frozenset((
    "numpy", "pandas", "scipy", "matplotlib", "seaborn", "plotly", "networkx", "statsmodels",
    "geopandas", "polars", "statistics", "statistical analysis", "data analysis",
    "data visualization", "data cleaning", "data wrangling", "tableau", "power bi", "excel",
    "looker", "beautifulsoup", "web scraping", "pyarrow", "duckdb",
))

_PRACTICE_NAMES = frozenset((
    "agile", "scrum", "kanban", "tdd", "test-driven development", "code review", "unit testing",
    "testing", "ci/cd", "devops", "mlops", "communication", "leadership", "teamwork",
    "collaboration", "project management", "technical writing", "public speaking",
    "mentoring", "pytest", "system design", "oop", "object-oriented programming",
    "version control", "documentation", "problem solving", "debugging",
))

# Buckets from agents.skill_postprocessor, mapped to areas. LANGS is a mixed bucket
# (languages, libraries and web frameworks); the name tables above carry the split,
# and what is left in it reads best as Languages.
_BUCKET_AREA = {LANGS: LANGUAGES, AIML: ML_AI, DATA: DATA_AREA, CLOUD: INFRA, TOOLS: TOOLS_AREA}

# Category words -> area, checked in this order against the category's tokens.
_CATEGORY_RULES = (
    (INFRA, {"cloud", "aws", "gcp", "azure", "devops", "infrastructure", "infra", "deployment",
             "containers", "kubernetes", "docker", "hosting", "serverless"}),
    (ML_AI, {"ml", "ai", "machine", "learning", "deep", "nlp", "llm", "llms", "technique",
             "techniques", "neural", "modeling", "modelling", "genai", "generative", "vision"}),
    (DATA_AREA, {"data", "database", "databases", "etl", "pipeline", "pipelines", "analytics",
                 "statistics", "visualization", "warehouse", "sql", "nosql", "storage"}),
    (WEB, {"web", "frontend", "front-end", "backend", "back-end", "mobile", "framework",
           "frameworks", "ui", "ux", "api", "apis", "app", "apps", "fullstack", "full-stack"}),
    (LANGUAGES, {"language", "languages", "programming", "library", "libraries"}),
    (PRACTICES, {"practice", "practices", "methodology", "methodologies", "process", "soft",
                 "management", "leadership", "communication", "testing"}),
    (TOOLS_AREA, {"tool", "tools", "tooling", "software", "platform", "platforms", "ide",
                  "editor", "version", "control", "productivity"}),
)

_TOKEN = re.compile(r"[a-z0-9+#]+(?:[-.][a-z0-9+#]+)*")


def _name_key(name: str) -> str:
    return normalize_skill_name(name or "").strip().lower()


def _area_from_name(name: str) -> Optional[str]:
    key = _name_key(name)
    raw = (name or "").strip().lower()
    for candidate in (key, raw):
        if not candidate:
            continue
        if candidate in _LANGUAGE_NAMES:
            return LANGUAGES
        if candidate in _PRACTICE_NAMES:
            return PRACTICES
        if candidate in _WEB_NAMES:
            return WEB
        if candidate in _DATA_SCIENCE_NAMES:
            return DATA_AREA
    bucket = SKILL_CATEGORIES.get(key) or SKILL_CATEGORIES.get(raw)
    return _BUCKET_AREA.get(bucket) if bucket else None


def _area_from_category(category: Optional[str]) -> Optional[str]:
    tokens = set(_TOKEN.findall((category or "").lower()))
    if not tokens:
        return None
    for area, words in _CATEGORY_RULES:
        if tokens & words:
            return area
    return None


def keyword_area(name: str, category: Optional[str] = None) -> str:
    """The keyword table's area for a skill: one of `AREAS`, `OTHER` when it cannot place it."""
    return _area_from_name(name) or _area_from_category(category) or OTHER


def skill_areas(pairs: Iterable[Tuple[str, Optional[str]]]) -> Dict[Tuple[str, Optional[str]], str]:
    """`{(name, category): area}` for every pair: the keyword table, then the Jev decision
    cache for the pairs the table leaves in `OTHER` (one query for all of them). Cache only,
    never a live call; a pair the cache does not hold stays `OTHER`."""
    pairs = list(pairs)
    out = {pair: keyword_area(*pair) for pair in pairs}
    unplaced = [pair for pair, area in out.items() if area == OTHER]
    if unplaced:
        from harness.decisions import skill_area as jev_area

        for pair, area in jev_area.cached(unplaced).items():
            out[pair] = area if area in AREAS else OTHER
    return out


def skill_area(name: str, category: Optional[str] = None) -> str:
    """The area a skill is listed under in the evidence map. Always one of `AREAS`."""
    area = keyword_area(name, category)
    return area if area != OTHER else skill_areas([(name, category)])[(name, category)]


def classify_unplaced_skills(user_id, *, client=None, dry_run: bool = False) -> Dict[str, int]:
    """Classify, with Jev, a user's skills that the keyword table leaves in `OTHER`.

    The write path for skills that were ingested before #318, or while Jev was off or had no
    key: `_save_skills` does the same for new ones. A skill already decided (including a
    decision of "Other") is read from the cache, so a second run makes no call. Returns
    `{skills, unplaced, classified, asked, fallback}`: the user's skills, those left in
    `OTHER`, those now placed in an area, how many answers Jev gave (not the cache), and
    how many fell back (no key, mode `off`, an API error). `dry_run` counts and asks nothing.
    """
    from sqlmodel import Session, select

    import database.db as _db
    from database.models import Skill, UserSkill

    with Session(_db.engine) as session:
        rows = session.exec(select(Skill).join(UserSkill, UserSkill.skill_id == Skill.skill_id)
                            .where(UserSkill.user_id == user_id)).all()
        pairs = sorted({(r.name, r.category) for r in rows}, key=lambda p: (p[0].lower(), p[0], p[1] or ""))
    unplaced = [p for p in pairs if keyword_area(*p) == OTHER]
    stats = {"skills": len(pairs), "unplaced": len(unplaced), "classified": 0, "asked": 0,
             "fallback": 0}
    if dry_run or not unplaced:
        return stats
    from harness.decisions import skill_area as jev_area

    for area, source in jev_area.classify(unplaced, client=client).values():
        stats["classified"] += 1 if area and area != OTHER else 0
        stats["asked"] += 1 if source == "jev" else 0
        stats["fallback"] += 1 if source == "fallback" else 0
    return stats


def classify_skills_at_write(skills: Iterable[Tuple[str, Optional[str]]], *, client=None) -> int:
    """Classify freshly written skills the keyword table cannot place; returns how many Jev
    answered. Best effort by contract: it never raises, so an ingest cannot fail or stall on
    it, and with no key, mode `off` or an API error it quietly places nothing."""
    try:
        unplaced = [p for p in skills if keyword_area(*p) == OTHER]
        if not unplaced:
            return 0
        from harness.decisions import skill_area as jev_area

        return sum(1 for _, source in jev_area.classify(unplaced, client=client).values()
                   if source == "jev")
    except Exception:
        return 0


# ── Classification when a key arrives after the skills did (#342) ─────────────────────────

BACKFILL_MARKER = "area_backfill.json"


def _data_dir():
    import os
    from pathlib import Path

    return Path(os.environ.get("ART_DATA_DIR") or Path.home() / ".art")


def _backfill_due(user_id, today: str, data_dir) -> Optional[str]:
    """The marker key to record when a startup backfill should run for this user, else None.

    Due when Jev can answer (mode is not `off`, a key resolves) and the user and key have not
    already had a backfill today. The key is identified by a hash, never stored. A marker that
    cannot be read counts as absent; nothing here raises."""
    import hashlib
    import json

    from harness.decisions import engine
    from harness.decisions.client import resolve_api_key

    if engine.mode() == "off":
        return None
    key = resolve_api_key()
    if not key:
        return None
    marker_key = f"{user_id}:{hashlib.sha256(key.encode()).hexdigest()[:12]}"
    try:
        marks = json.loads((data_dir / BACKFILL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marks = {}
    if isinstance(marks, dict) and marks.get(marker_key) == today:
        return None
    return marker_key


def _record_backfill(marker_key: str, today: str, data_dir) -> None:
    import json

    path = data_dir / BACKFILL_MARKER
    try:
        marks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marks = {}
    if not isinstance(marks, dict):
        marks = {}
    # Keep today's marks only: yesterday's say nothing, and the file stays small.
    marks = {k: v for k, v in marks.items() if v == today}
    marks[marker_key] = today
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marks), encoding="utf-8")


def backfill_unplaced_if_due(user_id, *, client=None, today: Optional[str] = None,
                             data_dir=None) -> Optional[Dict[str, int]]:
    """Run `classify_unplaced_skills` once per user and key per day, when Jev is available and
    the user has skills the table cannot place. Returns its stats, or None when it did not
    run. A profile ingested before a `TYPESAFE_API_KEY` was configured, or with Jev off, is
    thereby placed the next time a long-lived `artie` process starts with a key.

    The marker is written before the request, so two processes starting together, or a request
    that fails, do not retry until tomorrow or a different key. Never raises."""
    import datetime

    try:
        today = today or datetime.date.today().isoformat()
        data_dir = data_dir or _data_dir()
        marker_key = _backfill_due(user_id, today, data_dir)
        if marker_key is None:
            return None
        if classify_unplaced_skills(user_id, dry_run=True)["unplaced"] == 0:
            return None
        _record_backfill(marker_key, today, data_dir)
        return classify_unplaced_skills(user_id, client=client)
    except Exception:
        return None


def start_backfill_in_background(user_id, **kwargs):
    """`backfill_unplaced_if_due` on a daemon thread, so startup never waits on it (the
    database read, the request) and never fails because of it. Returns the thread, or None
    with no user. Called by `artie mcp` and `artie ui`, the long-lived processes, once their store
    is writable local SQLite (a remote database is backfilled by the script)."""
    if user_id is None:
        return None
    import threading

    thread = threading.Thread(target=backfill_unplaced_if_due, args=(user_id,), kwargs=kwargs,
                              name="art-area-backfill", daemon=True)
    thread.start()
    return thread
