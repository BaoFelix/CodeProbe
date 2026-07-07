"""
config.py — Configuration
═══════════════════════════════════════
Central management of paths, LLM backends, and API keys.
Priority: environment variables > .env file > defaults
(.env only fills keys the real environment hasn't set).
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
# Everything is anchored at PROJECT_ROOT, never the CWD: an external MCP
# host may launch us from an arbitrary directory, and CWD-relative paths
# would silently miss the DB / skills / default sources with no error.
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "refactor.db"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SKILLS_DIR = PROJECT_ROOT / "skills"

# ─── Source path ──────────────────────────────────────────────
SOURCE_ROOT = PROJECT_ROOT / "test_src"

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

# Per-request read timeout (seconds). Slow enterprise endpoints or big
# design-review prompts can exceed the default — raise LLM_TIMEOUT if you
# hit read timeouts. A timeout degrades gracefully (that call is skipped),
# it no longer crashes the run.
try:
    LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
except ValueError:
    LLM_TIMEOUT = 120

# Design-review pass-1 fans its independent subtree calls out concurrently
# (I/O-bound → a thread pool, not asyncio, is the simple+effective fit).
# LLM_MAX_WORKERS bounds the fan-out so a shared enterprise endpoint isn't
# hammered. LLM_REVIEW_DEADLINE (seconds, 0 = off) is a SOFT overall
# deadline: when it elapses, the review proceeds with whatever finished and
# leaves the rest for a resume run — so the user isn't blocked on stragglers.
try:
    LLM_MAX_WORKERS = max(1, int(os.environ.get("LLM_MAX_WORKERS", "6")))
except ValueError:
    LLM_MAX_WORKERS = 6
try:
    LLM_REVIEW_DEADLINE = int(os.environ.get("LLM_REVIEW_DEADLINE", "0"))
except ValueError:
    LLM_REVIEW_DEADLINE = 0
