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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # "openai" or "ollama"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Per-role model names (overridable via env)
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "gpt-4o-mini")
TAILOR_MODEL = os.getenv("TAILOR_MODEL", "gpt-4o-mini")

# Global Config
MODEL_NAME = "gpt-4o-mini"  # legacy alias — prefer role-specific vars above
EMBEDDING_MODEL = "all-MiniLM-L6-v2" # Standard HuggingFace model for Resume-Matcher style vectors

# Logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ART") # Agentic Resume Tailoring
