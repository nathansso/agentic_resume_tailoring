"""Derive the plumbing-mode canned parse from a profile fixture (issue #171).

`eval/tailoring_benchmark.py` used to carry `STUB_EXPERIENCES`, `STUB_PROJECTS`
and `STUB_SKILLS` as module-level constants whose own comment described them as
"canned parse of eval/profiles/benchmark_profile.md … kept aligned with the
fixture". That is a hand-maintained duplicate of the single profile that exists,
and it had already drifted: the markdown lists 32 skills, the constants listed
30 (PHP and the OpenAI API were missing).

The duplicate is also why a *second* profile could not exist — every new fixture
would need a second hand-copied parse, which is precisely the foundation #172's
dataset work needs. This module removes it: the markdown is the only source, and
any profile written in the same shape parses without touching Python.

What the markdown cannot state, this module derives by rule rather than by
per-profile fixture:

* **skill category** — from `SKILL_CATEGORIES`, a name-keyed dictionary shared
  across all profiles, defaulting to "Tool" on a miss. A new profile needs no
  entry added for it to parse; an entry only sharpens a category it already had
  a defensible default for.
* **skill proficiency** — a markdown resume states none, so it is derived from
  evidence density: how many bullets mention the skill (0 → 3, 1 → 4, 2+ → 5).
  Matching the *shape* of `agents/parser.py`'s own prompt ("3 if used in a
  project, 4 if used extensively"). A constant would be worse than a rule: with
  every proficiency equal, that scoring dimension (weight 0.10 in
  `skill_scorer.WEIGHTS`) would contribute nothing and the harness would
  silently stop exercising it.

Expected fixture shape (see eval/profiles/benchmark_profile.md):

    ## Experience

    **Company** — Title (Jan 2024 – Present)
    - bullet
    - bullet

    ## Projects

    **Name** (github.com/user/repo) — one-line descriptor
    - bullet

    ## Skills
    Python, TypeScript, ...
"""
import re
from pathlib import Path
from typing import Dict, List, Optional

# Name → category. Shared across every profile; a miss falls back to
# DEFAULT_CATEGORY rather than requiring an entry, so this is a dictionary, not
# a per-profile fixture.
DEFAULT_CATEGORY = "Tool"
SKILL_CATEGORIES: Dict[str, str] = {
    name.lower(): category
    for category, names in {
        "Language": ["Python", "TypeScript", "JavaScript", "C#", "C++", "Java",
                     "SQL", "PHP", "Go", "Rust", "Scala", "R", "Ruby", "Swift",
                     "Kotlin"],
        "Library": ["PyTorch", "XGBoost", "scikit-learn", "sentence-transformers",
                    "pandas", "NumPy", "FAISS", "TensorFlow", "Keras", "SciPy",
                    "Hugging Face", "Transformers"],
        "Framework": ["LangChain", "FastAPI", "Flask", "React", "Node.js",
                      "Django", "Vue", "Angular", "Next.js", "Spring",
                      "OpenAI API", "Express"],
        "Database": ["Postgres", "PostgreSQL", "MySQL", "Redis", "ClickHouse",
                     "Snowflake", "MongoDB", "Elasticsearch", "SQLite",
                     "DynamoDB", "BigQuery"],
        "Cloud": ["AWS", "GCP", "Azure", "Railway", "Heroku", "Vercel"],
        "Tool": ["Airflow", "Kafka", "Docker", "Kubernetes", "GitHub Actions",
                 "Nginx", "Unity", "Git", "Terraform", "Spark", "Jenkins",
                 "Grafana", "Prometheus"],
    }.items()
    for name in names
}

# Section headings this module understands. Anything else in the fixture
# (Summary, Education, Achievements) is parsed by the *real* pipeline in product
# and replay mode but has no canned payload — see the module note in
# tailoring_benchmark._stub_payload.
_EXPERIENCE_HEADING = "experience"
_PROJECT_HEADING = "projects"
_SKILL_HEADING = "skills"

_DASH = r"[—–-]"

_EXPERIENCE_RE = re.compile(
    rf"^\*\*(?P<company>[^*]+)\*\*\s*{_DASH}\s*(?P<title>[^(]+?)"
    rf"\s*\((?P<dates>[^)]*)\)\s*$"
)
_PROJECT_RE = re.compile(
    r"^\*\*(?P<name>[^*]+)\*\*"
    r"(?:\s*\((?P<url>[^)]*)\))?"
    rf"(?:\s*{_DASH}\s*(?P<description>.+?))?\s*$"
)
_BULLET_RE = re.compile(r"^[-•*]\s+(?P<text>.+?)\s*$")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_PRESENT = {"present", "current", "now"}


def _normalize_date(token: str) -> Optional[str]:
    """`Jan 2024` → `2024-01`; `2024` → `2024`; `Present` → `Present`."""
    token = token.strip()
    if not token:
        return None
    if token.lower() in _PRESENT:
        return "Present"
    if re.fullmatch(r"\d{4}-\d{2}", token):
        return token
    match = re.fullmatch(r"(?P<mon>[A-Za-z]{3,})\.?\s+(?P<year>\d{4})", token)
    if match:
        month = _MONTHS.get(match.group("mon")[:3].lower())
        if month:
            return f"{match.group('year')}-{month:02d}"
    if re.fullmatch(r"\d{4}", token):
        return token
    return token


def _split_dates(dates: str) -> tuple[Optional[str], Optional[str]]:
    parts = re.split(_DASH, dates, maxsplit=1)
    start = _normalize_date(parts[0]) if parts else None
    end = _normalize_date(parts[1]) if len(parts) > 1 else None
    return start, end


def _sections(text: str) -> Dict[str, List[str]]:
    """Fixture text → {lowercased `## heading`: its lines}."""
    out: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        if raw.startswith("## "):
            current = raw[3:].strip().lower()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(raw)
    return out


def _parse_experiences(lines: List[str]) -> List[Dict]:
    entries: List[Dict] = []
    for raw in lines:
        line = raw.strip()
        header = _EXPERIENCE_RE.match(line)
        if header:
            start, end = _split_dates(header.group("dates"))
            entries.append({
                "title": header.group("title").strip(),
                "company": header.group("company").strip(),
                "start_date": start,
                "end_date": end,
                # The fixture format carries no separate role summary — the
                # bullets are the content. Left None rather than invented.
                "description": None,
                "bullets": [],
            })
            continue
        bullet = _BULLET_RE.match(line)
        if bullet and entries:
            entries[-1]["bullets"].append(bullet.group("text"))
    return entries


def _parse_projects(lines: List[str]) -> List[Dict]:
    entries: List[Dict] = []
    for raw in lines:
        line = raw.strip()
        bullet = _BULLET_RE.match(line)
        if bullet:
            if entries:
                entries[-1]["bullets"].append(bullet.group("text"))
            continue
        if not line.startswith("**"):
            continue
        header = _PROJECT_RE.match(line)
        if not header:
            continue
        entry = {
            "name": header.group("name").strip(),
            "description": (header.group("description") or "").strip() or None,
            "bullets": [],
        }
        url = (header.group("url") or "").strip()
        if url:
            entry["repo_url"] = url if "://" in url else f"https://{url}"
        entries.append(entry)
    return entries


def _term_pattern(name: str) -> re.Pattern:
    # Same boundary rule as eval/metrics.py and agents/redundancy.py, so `SQL`
    # does not match inside `MySQL` or `SQLAlchemy`.
    return re.compile(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])")


def _parse_skills(lines: List[str], evidence: List[str]) -> List[Dict]:
    names: List[str] = []
    seen = set()
    for raw in lines:
        for chunk in raw.split(","):
            name = chunk.strip()
            if not name or name.startswith("#"):
                continue
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)

    corpus = [text.lower() for text in evidence]
    skills = []
    for name in names:
        pattern = _term_pattern(name)
        mentions = sum(1 for text in corpus if pattern.search(text))
        skills.append({
            "name": name,
            "category": SKILL_CATEGORIES.get(name.lower(), DEFAULT_CATEGORY),
            "proficiency": 5 if mentions >= 2 else 4 if mentions == 1 else 3,
        })
    return skills


class ProfileFixture:
    """The canned parse of one profile fixture, derived from its markdown."""

    def __init__(self, experiences: List[Dict], projects: List[Dict],
                 skills: List[Dict], source: Optional[Path] = None):
        self.experiences = experiences
        self.projects = projects
        self.skills = skills
        self.source = source

    @property
    def bullets(self) -> List[str]:
        """Every source bullet, experiences then projects."""
        return [b for item in (*self.experiences, *self.projects)
                for b in item["bullets"]]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"ProfileFixture(experiences={len(self.experiences)}, "
                f"projects={len(self.projects)}, skills={len(self.skills)}, "
                f"source={self.source})")


def parse_profile_text(text: str, source: Optional[Path] = None) -> ProfileFixture:
    sections = _sections(text)
    experiences = _parse_experiences(sections.get(_EXPERIENCE_HEADING, []))
    projects = _parse_projects(sections.get(_PROJECT_HEADING, []))
    evidence = [b for item in (*experiences, *projects) for b in item["bullets"]]
    evidence += [p["description"] for p in projects if p.get("description")]
    skills = _parse_skills(sections.get(_SKILL_HEADING, []), evidence)

    if not experiences or not projects or not skills:
        raise ValueError(
            "profile fixture parsed empty: "
            f"{len(experiences)} experiences, {len(projects)} projects, "
            f"{len(skills)} skills — check the '## Experience' / '## Projects' / "
            f"'## Skills' headings in {source or '<text>'}"
        )
    return ProfileFixture(experiences, projects, skills, source)


def load_profile(path: Path) -> ProfileFixture:
    path = Path(path)
    return parse_profile_text(path.read_text(encoding="utf-8"), source=path)
