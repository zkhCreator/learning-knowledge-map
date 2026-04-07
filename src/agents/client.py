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

import ast
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
    expect_json: bool = False,
) -> str:
    """
    Send a single system+user message pair and return the text response.

    Args:
        system:     System prompt for the agent.
        user:       User turn content.
        max_tokens: Token budget for the response.
        model:      Override the default model from config.
        expect_json: Hint that the caller expects JSON output. Used to enable
                     provider-specific compatibility features where possible.

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
        finish_reason = getattr(response, "stop_reason", None)
    else:
        request_kwargs: dict[str, Any] = {
            "model": used_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if expect_json:
            request_kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            if expect_json and _is_unsupported_json_mode_error(exc):
                log.warning(
                    "OpenAI-compatible endpoint rejected response_format=json_object; retrying without JSON mode"
                )
                request_kwargs.pop("response_format", None)
                response = client.chat.completions.create(**request_kwargs)
            else:
                raise

        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        text = _extract_openai_message_text(message)
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", "?")
        output_tokens = getattr(usage, "completion_tokens", "?")
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
    elapsed = time.perf_counter() - t0

    log.debug(
        "── API RESPONSE (%.2fs) ──────────────────────────\n"
        "input_tokens : %s  output_tokens : %s  finish_reason : %s\n"
        "response     :\n%s\n"
        "──────────────────────────────────────────────────",
        elapsed,
        input_tokens,
        output_tokens,
        finish_reason,
        _indent(text, 4),
    )
    if not text.strip():
        log.warning(
            "Model returned empty text content (provider=%s, model=%s, finish_reason=%s, output_tokens=%s)",
            provider,
            used_model,
            finish_reason,
            output_tokens,
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
    raw = call(system, user, max_tokens=max_tokens, model=model, expect_json=True)
    return _extract_json(raw)


def _extract_json(text: str) -> dict | list:
    """
    Extract JSON from a model response that may be wrapped in markdown fences
    or expressed as JSON-like Python literals.

    Strategy:
      1. Try fenced blocks first
      2. Try the whole text as-is
      3. Try the first balanced {...} or [...] substring
      4. For each candidate, parse as strict JSON, then as a Python literal
    """
    if not text or not text.strip():
        raise ValueError("Model returned empty response when JSON was expected.")

    candidates: list[str] = []

    def add_candidate(candidate: str):
        candidate = candidate.strip().lstrip("\ufeff")
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for fence_match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE):
        add_candidate(fence_match.group(1))
    add_candidate(text)

    balanced = _find_balanced_json_substring(text)
    if balanced:
        add_candidate(balanced)

    for candidate in candidates:
        parsed = _parse_json_like(candidate)
        if parsed is not None:
            return parsed

        inner_balanced = _find_balanced_json_substring(candidate)
        if inner_balanced and inner_balanced != candidate:
            parsed = _parse_json_like(inner_balanced)
            if parsed is not None:
                return parsed

    raise ValueError(f"Could not extract JSON from model response:\n{text[:500]}")


def _parse_json_like(text: str) -> dict | list | None:
    stripped = text.strip().lstrip("\ufeff")
    if not stripped:
        return None

    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(stripped)
        except (json.JSONDecodeError, SyntaxError, ValueError):
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _find_balanced_json_substring(text: str) -> str | None:
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start == -1:
            continue

        depth = 0
        quote_char: str | None = None
        escape = False

        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\" and quote_char:
                escape = True
                continue
            if ch in ('"', "'"):
                if quote_char is None:
                    quote_char = ch
                    continue
                if quote_char == ch:
                    quote_char = None
                    continue
            if quote_char is not None:
                continue

            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _extract_openai_message_text(message: Any) -> str:
    if not message:
        return ""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue

            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _is_unsupported_json_mode_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "response_format" in message or "json_object" in message
