"""Skill areas for the evidence map (issue #136).

`Skill.category` is free text ("Programming Language", "Languages & Libraries",
"Technique", ...), far too many values to group a skills column by. This maps a
skill to one of a fixed, small set of areas with a keyword table. It is a pure
function of `(name, category)`: no model call, no storage, the same answer on
every request. #318 replaces the keyword table with a cached Jev call; callers
only ever see `skill_area`, so that swap stays inside this module.

Order of evidence, most specific first:
1. the skill's name, against explicit name tables (a language is a language
   whatever category an ingest gave it);
2. the curated name -> bucket map in `agents.skill_postprocessor`;
3. the free-text category, tokenized so "ai" does not match "email";
4. "Other".
"""
import re
from typing import Optional

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


def skill_area(name: str, category: Optional[str] = None) -> str:
    """The area a skill is listed under in the evidence map. Always one of `AREAS`."""
    return _area_from_name(name) or _area_from_category(category) or OTHER
