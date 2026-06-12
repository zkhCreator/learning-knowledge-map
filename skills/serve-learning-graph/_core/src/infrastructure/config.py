"""
File: src/infrastructure/config.py

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

# Load .env from the current working directory first (installed-skill mode),
# then from the repo root (development mode). See docs/20 and docs/22.
try:
    from dotenv import load_dotenv
    for _env_path in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if _env_path.exists():
            load_dotenv(_env_path)
            break
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


# ── Web data-plane mode ────────────────────────────────────────────────────────

# When set (env LDG_WEB_MODE=1), the process is serving the local GUI as a pure
# data plane: it may read/write SQLite but MUST NOT call the LLM. Any agent
# capability request is refused at the client boundary so the GUI can hand the
# generation step back to Codex / Claude Code instead of leaking a raw
# "package not installed" / missing-key error. See docs/16-web-data-plane-handoff.
def _env_truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in ("", "0", "false", "no", "off")


WEB_MODE: bool = _env_truthy(os.environ.get("LDG_WEB_MODE"))


# ── Database ───────────────────────────────────────────────────────────────────

# Canonical resolution rule (docs/20): explicit argument > DB_PATH env var >
# Path.cwd()/"learning.db". The skill scripts that must stay stdlib-only
# (export_graph.py / print_graph.py) duplicate this rule on purpose; the shared
# tests in tests/test_db_path_resolution.py pin both copies together.

DEFAULT_DB_FILENAME = "learning.db"
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def resolve_db_path(explicit: str | Path | None = None, cwd: Path | None = None) -> Path:
    """
    Resolve the SQLite database location.

    - explicit file path (.db/.sqlite/.sqlite3 suffix): used as-is
    - explicit directory (or suffix-less path): that directory / learning.db
    - no explicit path: DB_PATH env var, else <cwd>/learning.db
    """
    base = (cwd or Path.cwd()).resolve()
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        if candidate.suffix.lower() in SQLITE_SUFFIXES:
            return candidate
        return candidate / DEFAULT_DB_FILENAME

    env_path = os.environ.get("DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return base / DEFAULT_DB_FILENAME


DB_PATH: Path = resolve_db_path()


# ── Learning system tuning ────────────────────────────────────────────────────

MAX_DECOMPOSE_DEPTH: int = int(os.environ.get("MAX_DECOMPOSE_DEPTH", "6"))
MAX_DECOMPOSE_RETRIES: int = int(os.environ.get("MAX_DECOMPOSE_RETRIES", "2"))
ATOM_MAX_MINUTES: int = int(os.environ.get("ATOM_MAX_MINUTES", "15"))

REVIEW_INTERVALS: list[int] = [1, 3, 7, 14, 30, 90]

# Tool-side exam quality gates (docs/23). Both default on; set to 0 to skip.
# The web data plane never reaches these code paths (LDG_WEB_MODE gate).
EXAM_VALIDATE: bool = _env_truthy(os.environ.get("EXAM_VALIDATE", "1"))
EXAM_SPOT_CHECK: bool = _env_truthy(os.environ.get("EXAM_SPOT_CHECK", "1"))

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
