import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base Paths
BASE_DIR = Path(__file__).resolve().parent

# App data directory — all user data lives under ~/.art/ (override via ART_DATA_DIR for Docker)
APP_DATA_DIR = Path(os.getenv("ART_DATA_DIR", str(Path.home() / ".art")))
EXPORTS_DIR = APP_DATA_DIR / "exports"
UPLOADS_DIR = APP_DATA_DIR / "uploads"
LOGS_DIR    = APP_DATA_DIR / "logs"

# Database — defaults to local SQLite; set DATABASE_URL to use PostgreSQL (e.g. Supabase)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{APP_DATA_DIR / 'art.db'}")


def ensure_app_dirs(base_dir: Path | None = None) -> None:
    """Create all required application directories. Idempotent."""
    base = base_dir or APP_DATA_DIR
    for sub in ("", "exports", "uploads", "logs"):
        (base / sub if sub else base).mkdir(parents=True, exist_ok=True)

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # stub for PRD 05
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")

# Bright Data LinkedIn scraping (issue 13) — platform-wide key; LinkedIn
# ingestion is disabled (falls back to PDF upload) when the key is unset.
BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY")
BRIGHTDATA_LINKEDIN_DATASET_ID = os.getenv(
    "BRIGHTDATA_LINKEDIN_DATASET_ID", "gd_l1viktl72bvl7bjuj0"
)

# LLM Provider Config
def normalize_provider(value: str | None) -> str:
    """Canonicalise an LLM_PROVIDER value: trim, unquote, lowercase.

    Env values reach us quoted more often than you'd think — `set_key()` writes
    `LLM_PROVIDER='anthropic'` to .env, a shell `export LLM_PROVIDER="'x'"`
    keeps the inner quotes, and hosted secret stores (Fly, Railway) preserve
    whatever was pasted in. python-dotenv strips quotes when it parses .env, but
    nothing strips them off a value that was already in the process environment,
    which surfaced as `Unknown LLM_PROVIDER: "'anthropic'"`. Normalising at the
    read sites means an extra pair of quotes can never take the app down again.
    """
    if not value:
        return "anthropic"
    return value.strip().strip("\"'").strip().lower() or "anthropic"


LLM_PROVIDER = normalize_provider(os.getenv("LLM_PROVIDER"))  # "anthropic" or "openai"

# Per-role model names (overridable via env)
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-4-5-20251001")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-haiku-4-5-20251001")
TAILOR_MODEL = os.getenv("TAILOR_MODEL", "claude-sonnet-4-6")
EVAL_MODEL = os.getenv("EVAL_MODEL", CHAT_MODEL)
REVIEW_MODEL = os.getenv("REVIEW_MODEL", TAILOR_MODEL)

# ── Observability: LangSmith tracing scaffold (issue #142) ───────────────────
# OFF by default, wired for P3 RL. LangChain auto-reads LANGCHAIN_TRACING_V2 /
# LANGCHAIN_API_KEY / LANGCHAIN_PROJECT from the environment, so tracing turns on
# ONLY when the operator sets those env vars. This scaffold documents and
# surfaces them — it never force-enables them (nothing here writes to os.environ).
# Because extraction now stays on the LangChain path (get_extractor), a single
# trace covers extraction + chat + tailor uniformly.
#
# PII CAVEAT: traces carry full resume + JD text (PII). Before enabling in
# production, self-host LangSmith or set a data region and scrub the payloads.
# Do NOT enable in prod until P3.
LANGSMITH_API_KEY = os.getenv("LANGCHAIN_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT")

_TRUTHY = {"1", "true", "yes", "on"}


def langsmith_enabled() -> bool:
    """True only when LangSmith tracing is explicitly enabled via env.

    Reads the environment live so a test or runtime toggle is reflected, and
    never enables tracing as a side effect of being called.
    """
    val = os.getenv("LANGCHAIN_TRACING_V2")
    return bool(val) and val.strip().strip("\"'").lower() in _TRUTHY


# Global Config
MODEL_NAME = "gpt-4o-mini"  # legacy alias for openai fallback
EMBEDDING_MODEL = "all-MiniLM-L6-v2" # Standard HuggingFace model for Resume-Matcher style vectors

# Logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ART") # Agentic Resume Tailoring
