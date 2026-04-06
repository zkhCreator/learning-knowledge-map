"""
File: agents/client.py

Purpose:
    Thin wrapper around the Anthropic SDK that supports custom base URLs
    (for self-hosted or proxy deployments) and provides a simple call()
    interface used by all agents in this project.

Responsibilities:
    - Initialise the Anthropic client once (singleton)
    - Expose a synchronous call() helper that returns the text response
    - Handle JSON extraction from model responses
    - Surface clear error messages when the API is misconfigured

What this file does NOT do:
    - Business logic or prompt construction (that lives in each agent file)
    - Retry logic beyond what the SDK provides
    - Streaming (not needed for CLI batch calls)

Inputs:
    - ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL from config
    - system prompt, user messages, max_tokens per call

Outputs:
    - Raw text string from the model
"""

import json
import re
import time
from typing import Optional

import anthropic

from src import config
from src.logger import get_logger

log = get_logger(__name__)

# ── Singleton client ───────────────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        kwargs: dict = {"api_key": config.ANTHROPIC_API_KEY}
        if config.ANTHROPIC_BASE_URL:
            kwargs["base_url"] = config.ANTHROPIC_BASE_URL.rstrip("/")
            log.info("Using custom base URL: %s", config.ANTHROPIC_BASE_URL)
        _client = anthropic.Anthropic(**kwargs)
        log.debug("Anthropic client initialised (model=%s)", config.ANTHROPIC_MODEL)
    return _client


# ── Core call helper ───────────────────────────────────────────────────────────

def call(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """
    Send a single system+user message pair to Claude and return the text response.

    Args:
        system:     System prompt for the agent.
        user:       User turn content.
        max_tokens: Token budget for the response.
        model:      Override the default model from config.

    Returns:
        The model's text response as a string.
    """
    used_model = model or config.ANTHROPIC_MODEL
    client = get_client()

    log.debug(
        "── API CALL ──────────────────────────────────────\n"
        "model      : %s\n"
        "max_tokens : %d\n"
        "system     :\n%s\n"
        "user       :\n%s\n"
        "──────────────────────────────────────────────────",
        used_model, max_tokens,
        _indent(system, 4),
        _indent(user, 4),
    )

    t0 = time.perf_counter()
    response = client.messages.create(
        model=used_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    elapsed = time.perf_counter() - t0

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text = block.text
            break

    usage = response.usage
    log.debug(
        "── API RESPONSE (%.2fs) ──────────────────────────\n"
        "input_tokens : %d  output_tokens : %d\n"
        "response     :\n%s\n"
        "──────────────────────────────────────────────────",
        elapsed,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        _indent(text, 4),
    )

    return text


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
