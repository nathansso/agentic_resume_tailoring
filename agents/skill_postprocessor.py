"""
Skill Post-Processing — Filters and normalizes extracted skills.

Removes noise like internal module names, stdlib leaks, and duplicates.
Merges near-duplicate skill names (e.g. 'sklearn' and 'scikit-learn').
"""
import logging
import re
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Known aliases → canonical name
SKILL_ALIASES = {
    "sklearn": "scikit-learn",
    "sk-learn": "scikit-learn",
    "sci-kit learn": "scikit-learn",
    "numpy": "NumPy",
    "pandas": "pandas",
    "matplotlib": "Matplotlib",
    "seaborn": "seaborn",
    "pytorch": "PyTorch",
    "torch": "PyTorch",
    "torchvision": "PyTorch",
    "torchnet": "PyTorch",
    "tensorflow": "TensorFlow",
    "tf": "TensorFlow",
    "xgboost": "XGBoost",
    "lightgbm": "LightGBM",
    "geopandas": "GeoPandas",
    "beautifulsoup4": "BeautifulSoup",
    "bs4": "BeautifulSoup",
    "sqlalchemy": "SQLAlchemy",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mongodb": "MongoDB",
    "mongo": "MongoDB",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
    "java": "Java",
    "html": "HTML",
    "css": "CSS",
    "r": "R",
    "sql": "SQL",
    "scipy": "SciPy",
    "plotly": "Plotly",
    "networkx": "NetworkX",
    "statsmodels": "statsmodels",
    "nltk": "NLTK",
    "spacy": "spaCy",
    "opencv": "OpenCV",
    "cv2": "OpenCV",
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "openai": "OpenAI API",
    "pydantic": "Pydantic",
    "sqlmodel": "SQLModel",
    "sqlite3": "SQLite",
}

# Skills to always reject — internal modules, stdlib leaks, non-skills
REJECT_PATTERNS = [
    # Python stdlib that might slip through
    r"^(os|sys|re|json|math|random|datetime|collections|itertools|functools)$",
    r"^(pathlib|typing|abc|io|time|copy|warnings|logging|unittest|string)$",
    r"^(csv|hashlib|struct|operator|contextlib|tempfile|glob|shutil|pickle)$",
    r"^(subprocess|threading|multiprocessing|socket|http|urllib|email)$",
    r"^(pdb|traceback|inspect|dis|gc|weakref|enum|dataclasses|argparse)$",
    r"^(statistics|decimal|fractions|numbers|zipfile|getpass|uuid|base64)$",
    r"^(codecs|configparser|pprint|heapq|queue|signal|ctypes|platform)$",
    r"^(site|textwrap|xml|html|struct)$",
    # Common non-skill imports
    r"^(dotenv|python-dotenv|python-docx)$",
    # Internal/local module patterns
    r"_funcs$",       # missingness_funcs, outlier_funcs, etc.
    r"^dsc\d",        # dsc40graph, dsc207, etc. (course-specific)
    r"^disjoint_",    # disjoint_forest
    r"^team_",        # team_utils
    # Too generic to be useful
    r"^(otter|elocuent)$",
    # Single characters (except R)
    r"^[a-qs-z]$",
]

# Compiled reject patterns
_REJECT_RE = [re.compile(p, re.IGNORECASE) for p in REJECT_PATTERNS]


def normalize_skill_name(name: str) -> str:
    """Normalize a skill name using the alias map."""
    stripped = name.strip()
    key = stripped.lower()
    return SKILL_ALIASES.get(key, stripped)


def should_reject_skill(name: str) -> bool:
    """Check if a skill name should be filtered out."""
    stripped = name.strip()
    if not stripped or len(stripped) < 2:
        return True
    for pattern in _REJECT_RE:
        if pattern.search(stripped):
            return True
    return False


def postprocess_skills(skills: List[Dict]) -> List[Dict]:
    """
    Filter and normalize a list of skill dicts.
    
    - Removes rejected skills (stdlib, internal modules, noise)
    - Normalizes names via alias map
    - Deduplicates (keeps highest proficiency)
    
    Args:
        skills: List of dicts with 'name', 'category', 'proficiency'
        
    Returns:
        Cleaned list of skill dicts
    """
    seen = {}  # canonical_name_lower -> skill dict

    for skill in skills:
        raw_name = skill.get("name", "").strip()
        if not raw_name:
            continue

        # Check rejection
        if should_reject_skill(raw_name):
            logger.debug(f"Rejecting skill: {raw_name}")
            continue

        # Normalize
        canonical = normalize_skill_name(raw_name)
        key = canonical.lower()

        # Dedup — keep highest proficiency
        if key in seen:
            existing_prof = seen[key].get("proficiency", 0)
            new_prof = skill.get("proficiency", 0)
            if new_prof > existing_prof:
                seen[key]["proficiency"] = new_prof
        else:
            seen[key] = {
                "name": canonical,
                "category": skill.get("category", "Other"),
                "proficiency": skill.get("proficiency", 1),
            }

    result = list(seen.values())
    logger.info(f"Post-processed skills: {len(skills)} -> {len(result)}")
    return result
