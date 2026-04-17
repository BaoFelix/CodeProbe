"""
config.py — Configuration
═══════════════════════════════════════
Central management of paths, LLM backends, and API keys.
Priority: .env file > environment variables > defaults.
═══════════════════════════════════════
"""
from pathlib import Path
import os

# ─── Load .env file ─────────────────────────────────────────
# Zero dependencies: parse KEY=VALUE manually instead of python-dotenv
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            # .env has lower priority than real env vars (allow override)
            if key and key not in os.environ:
                os.environ[key] = value

# ─── Project paths ───────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "refactor.db"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# ─── Source path ──────────────────────────────────────────────
SOURCE_ROOT = Path("test_src")

# ─── LLM backend config ────────────────────────────────────────────
# LLM_API_FORMAT determines request format:
#   "openai"    = OpenAI-compatible (GitHub Models / any compatible endpoint)
#   "anthropic" = Anthropic Messages API (Claude)
#
# === Claude API ===
#   LLM_API_FORMAT = "anthropic"
#   LLM_API_URL    = "https://api.anthropic.com"
#   LLM_API_KEY    = sk-ant-... (from console.anthropic.com)
#   LLM_MODEL      = "claude-sonnet-4-20250514"
#
# === GitHub Models (free) ===
#   LLM_API_FORMAT = "openai"
#   LLM_API_URL    = "https://models.inference.ai.azure.com"
#   LLM_API_KEY    = your GitHub Token
#   LLM_MODEL      = "gpt-4o-mini"
#
LLM_API_FORMAT = os.environ.get("LLM_API_FORMAT", "openai")
LLM_API_URL = os.environ.get("LLM_API_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

# 429 fallback chain: high to low priority; auto-fallback on rate limit
# Supports custom env var (comma-separated), defaults sorted by GitHub Models free model capability
LLM_FALLBACK_MODELS = [
    m.strip() for m in
    os.environ.get("LLM_FALLBACK_MODELS",
                   "gpt-4o,gpt-4o-mini,Meta-Llama-3.1-405B-Instruct,Phi-4").split(",")
]

# ─── Analysis settings ─────────────────────────────────────────────────
# Files exceeding this line count use Code Query summary mode before analysis (RAG mode)
MAX_LINES_DIRECT = 500

# C++ file extensions
HEADER_EXTS = {'.h', '.hxx', '.hpp', '.sch'}
IMPL_EXTS = {'.cpp', '.cxx', '.c'}

# Target module for analysis
DEFAULT_MODULE = os.environ.get("CODEPROBE_MODULE", "default")
