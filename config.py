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

# LLM Provider Config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic" or "openai"

# Per-role model names (overridable via env)
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-4-5-20251001")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "claude-haiku-4-5-20251001")
TAILOR_MODEL = os.getenv("TAILOR_MODEL", "claude-sonnet-4-6")
EVAL_MODEL = os.getenv("EVAL_MODEL", CHAT_MODEL)
REVIEW_MODEL = os.getenv("REVIEW_MODEL", TAILOR_MODEL)

# Global Config
MODEL_NAME = "gpt-4o-mini"  # legacy alias for openai fallback
EMBEDDING_MODEL = "all-MiniLM-L6-v2" # Standard HuggingFace model for Resume-Matcher style vectors

# Logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ART") # Agentic Resume Tailoring
