"""
File: config.py

Purpose:
    Centralised configuration loader for the learning system.

Responsibilities:
    - Load environment variables from .env file
    - Expose typed config values to the rest of the application
    - Validate required settings at startup

What this file does NOT do:
    - Business logic
    - Database operations
    - Agent calls
"""

import os
from pathlib import Path

# Load .env from project root
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass  # dotenv optional; env vars may already be set


# ── Anthropic ──────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL: str | None = os.environ.get("ANTHROPIC_BASE_URL") or None


# ── OpenAI (and compatible providers) ─────────────────────────────────────────

OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
# Base URL for OpenAI or any OpenAI-compatible endpoint (Ollama, DeepSeek, etc.)
# Default points to official OpenAI; override for custom endpoints.
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")


# ── Default model ──────────────────────────────────────────────────────────────

# Which model to use when --model is not specified on the CLI.
# Provider is auto-detected from the model name prefix:
#   claude-*  →  Anthropic
#   anything else  →  OpenAI-compatible
DEFAULT_MODEL: str = os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")

# Back-compat alias used internally (agents read this at call time)
ANTHROPIC_MODEL: str = DEFAULT_MODEL  # kept for legacy references


# ── Database ───────────────────────────────────────────────────────────────────

_project_root = Path(__file__).parent.parent
DB_PATH: Path = Path(os.environ.get("DB_PATH", str(_project_root / "data" / "learning.db")))


# ── Learning system tuning ────────────────────────────────────────────────────

MAX_DECOMPOSE_DEPTH: int = int(os.environ.get("MAX_DECOMPOSE_DEPTH", "6"))
MAX_DECOMPOSE_RETRIES: int = int(os.environ.get("MAX_DECOMPOSE_RETRIES", "2"))
ATOM_MAX_MINUTES: int = int(os.environ.get("ATOM_MAX_MINUTES", "15"))

REVIEW_INTERVALS: list[int] = [1, 3, 7, 14, 30, 90]

MASTERY_THRESHOLDS: dict[str, float] = {
    "critical": 0.95,
    "standard": 0.80,
    "familiarity": 0.60,
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def provider_for(model: str) -> str:
    """Return 'anthropic' or 'openai' based on model name prefix."""
    return "anthropic" if model.strip().startswith("claude") else "openai"


def validate(model: str | None = None):
    """Raise early if required config for the chosen provider is missing."""
    used_model = model or DEFAULT_MODEL
    provider = provider_for(used_model)

    if provider == "anthropic":
        if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-xxx"):
            raise EnvironmentError(
                "ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file: ANTHROPIC_API_KEY=sk-ant-..."
            )
    else:
        if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-xxx"):
            raise EnvironmentError(
                f"OPENAI_API_KEY is not set (required for model '{used_model}'). "
                "Add it to your .env file: OPENAI_API_KEY=sk-..."
            )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
