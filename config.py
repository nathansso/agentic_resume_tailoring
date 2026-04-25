import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base Paths
BASE_DIR = Path(__file__).resolve().parent

# Database Config — SQLite (zero setup, single file)
DATABASE_URL = f"sqlite:///{BASE_DIR / 'art.db'}"

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # stub for PRD 05
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")

# LLM Provider Config
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic", "openai", or "ollama"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

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
