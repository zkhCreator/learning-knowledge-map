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
_openai_base_url_env = os.environ.get("OPENAI_BASE_URL")
# Base URL for official OpenAI or any OpenAI-compatible endpoint.
# Accepts either a full API prefix (for example ".../v1") or a bare forwarded
# domain; the client normalises bare domains to the OpenAI-style /v1 path.
OPENAI_BASE_URL: str = _openai_base_url_env or "https://api.openai.com"

# Shared proxy credentials for third-party relay services.
# When set, both OpenAI and Anthropic protocol clients reuse the same values.
LLM_API_KEY: str = os.environ.get("LLM_API_KEY") or ANTHROPIC_API_KEY or OPENAI_API_KEY
LLM_BASE_URL: str | None = (
    os.environ.get("LLM_BASE_URL")
    or ANTHROPIC_BASE_URL
    or _openai_base_url_env
    or None
)


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
    """Return 'anthropic' or 'openai' based on the model name prefix."""
    return "anthropic" if model.strip().startswith("claude") else "openai"


def api_key_for(provider: str) -> str:
    """Return the API key for the selected provider, preferring shared relay config."""
    if LLM_API_KEY:
        return LLM_API_KEY
    return ANTHROPIC_API_KEY if provider == "anthropic" else OPENAI_API_KEY


def base_url_for(provider: str) -> str | None:
    """Return the base URL for the selected provider, preferring shared relay config."""
    if LLM_BASE_URL:
        return LLM_BASE_URL
    return ANTHROPIC_BASE_URL if provider == "anthropic" else OPENAI_BASE_URL


def _is_placeholder_key(api_key: str) -> bool:
    return (
        not api_key
        or api_key.startswith("sk-xxx")
        or api_key.startswith("sk-ant-xxx")
    )


def validate(model: str | None = None):
    """Raise early if required config for the chosen provider is missing."""
    used_model = model or DEFAULT_MODEL
    provider = provider_for(used_model)
    api_key = api_key_for(provider)

    if _is_placeholder_key(api_key):
        if provider == "anthropic":
            raise EnvironmentError(
                f"API key is not set for model '{used_model}'. "
                "Add LLM_API_KEY or ANTHROPIC_API_KEY to your .env file."
            )
        raise EnvironmentError(
            f"API key is not set for model '{used_model}'. "
            "Add LLM_API_KEY or OPENAI_API_KEY to your .env file."
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
