"""
File: agents/client.py

Purpose:
    Provider-aware wrapper around the Anthropic SDK and OpenAI-compatible
    chat-completions APIs. Supports custom base URLs for self-hosted,
    proxy, or third-party forwarded domains while preserving the same
    call() interface used by all agents in this project.

Responsibilities:
    - Initialise provider clients once (singleton per transport)
    - Expose a synchronous call() helper that returns the text response
    - Handle JSON extraction from model responses
    - Surface clear error messages when the API is misconfigured

What this file does NOT do:
    - Business logic or prompt construction (that lives in each agent file)
    - Retry logic beyond what the SDK provides
    - Streaming (not needed for CLI batch calls)
    - Provider-specific advanced features beyond plain text generation

Inputs:
    - Shared or provider-specific config from src.config
    - system prompt, user messages, max_tokens per call

Outputs:
    - Raw text string from the model
"""

import json
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import anthropic
except ImportError:  # pragma: no cover - depends on runtime environment
    anthropic = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - depends on runtime environment
    OpenAI = None

from src import config
from src.logger import get_logger

log = get_logger(__name__)

# ── Singleton clients ──────────────────────────────────────────────────────────

_anthropic_client: Any = None
_openai_client: Any = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        if anthropic is None:
            raise ModuleNotFoundError(
                "anthropic package is not installed. Install dependencies from requirements.txt."
            )

        kwargs: dict[str, Any] = {"api_key": config.api_key_for("anthropic")}
        base_url = config.base_url_for("anthropic")
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
            log.info("Using Anthropic base URL: %s", base_url)
        _anthropic_client = anthropic.Anthropic(**kwargs)
        log.debug("Anthropic client initialised")
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        if OpenAI is None:
            raise ModuleNotFoundError(
                "openai package is not installed. Install dependencies from requirements.txt."
            )

        kwargs: dict[str, Any] = {"api_key": config.api_key_for("openai")}
        base_url = _normalise_openai_base_url(config.base_url_for("openai") or "")
        if base_url:
            kwargs["base_url"] = base_url
            log.info("Using OpenAI-compatible base URL: %s", base_url)
        _openai_client = OpenAI(**kwargs)
        log.debug("OpenAI-compatible client initialised")
    return _openai_client


def get_client(model: str | None = None):
    provider = config.provider_for(model or config.DEFAULT_MODEL)
    if provider == "anthropic":
        return _get_anthropic_client()
    return _get_openai_client()


# ── Core call helper ───────────────────────────────────────────────────────────

def call(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """
    Send a single system+user message pair and return the text response.

    Args:
        system:     System prompt for the agent.
        user:       User turn content.
        max_tokens: Token budget for the response.
        model:      Override the default model from config.

    Returns:
        The model's text response as a string.
    """
    used_model = model or config.DEFAULT_MODEL
    provider = config.provider_for(used_model)
    client = get_client(used_model)

    log.debug(
        "── API CALL ──────────────────────────────────────\n"
        "provider   : %s\n"
        "model      : %s\n"
        "max_tokens : %d\n"
        "system     :\n%s\n"
        "user       :\n%s\n"
        "──────────────────────────────────────────────────",
        provider,
        used_model,
        max_tokens,
        _indent(system, 4),
        _indent(user, 4),
    )

    t0 = time.perf_counter()
    if provider == "anthropic":
        response = client.messages.create(
            model=used_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
                break
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", "?")
        output_tokens = getattr(usage, "output_tokens", "?")
    else:
        response = client.chat.completions.create(
            model=used_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        message = response.choices[0].message if response.choices else None
        text = message.content if message and message.content else ""
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", "?")
        output_tokens = getattr(usage, "completion_tokens", "?")
    elapsed = time.perf_counter() - t0

    log.debug(
        "── API RESPONSE (%.2fs) ──────────────────────────\n"
        "input_tokens : %s  output_tokens : %s\n"
        "response     :\n%s\n"
        "──────────────────────────────────────────────────",
        elapsed,
        input_tokens,
        output_tokens,
        _indent(text, 4),
    )

    return text


def _normalise_openai_base_url(base_url: str) -> str:
    """
    Accept either a full OpenAI-compatible API prefix or a bare forwarded domain.
    Bare domains are normalised to /v1 so reverse proxies can be configured as
    https://llm.example.com -> https://api.openai.com/v1.
    """
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return "https://api.openai.com/v1"

    parsed = urlsplit(cleaned)
    if not parsed.path or parsed.path == "/":
        return urlunsplit(parsed._replace(path="/v1"))
    return cleaned


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


def call_json(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> dict | list:
    """
    Like call(), but expects JSON output from the model.
    Strips markdown code fences if present and parses the JSON.

    Raises:
        ValueError: if the response cannot be parsed as JSON.
    """
    raw = call(system, user, max_tokens=max_tokens, model=model)
    return _extract_json(raw)


def _extract_json(text: str) -> dict | list:
    """
    Extract JSON from a model response that may be wrapped in markdown fences.

    Strategy:
      1. Try to find a ```json ... ``` block
      2. Try to find a ``` ... ``` block
      3. Try to parse the whole text as-is
      4. Try to find the first {...} or [...] balanced substring
    """
    # Strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try raw text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Find first JSON object or array
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if not in_string:
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break

    raise ValueError(f"Could not extract JSON from model response:\n{text[:500]}")
