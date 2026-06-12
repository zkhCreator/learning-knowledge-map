"""
File: src/infrastructure/web_link.py

Purpose:
    Build a clickable deep link back into the local GUI workflow host after a
    generation skill (run in Codex / Claude Code) has persisted its output.

Responsibilities:
    - Resolve the live host base URL: sidecar file written by serve-learning-graph
      on startup, then the LDG_WEB_URL env override, then a localhost default.
    - Compose a ?view=…&<resource>=<id> URL for a given flow and resource id.

What this file does NOT do:
    - Start or contact the server (it only reads the sidecar the server writes)
    - Access SQLite or call the LLM
    - Guarantee the server is running — callers print the link best-effort

Inputs: optional view + resource ids (exam / node / goal / review / session)
Outputs: an absolute URL string (or a relative ?view=… path if no base is known)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlencode

# .web_url sidecar, written by skills/serve-learning-graph/scripts/server.js
# next to the resolved database file (docs/20). Tests may monkeypatch
# SIDECAR_PATH to a concrete path; None means "follow config.DB_PATH lazily".
SIDECAR_PATH: Path | None = None

DEFAULT_BASE_URL = "http://localhost:8765"


def _sidecar_path() -> Path:
    if SIDECAR_PATH is not None:
        return SIDECAR_PATH
    from src.infrastructure import config

    return Path(config.DB_PATH).parent / ".web_url"

# Map skill-friendly resource kwargs to the URL params the web App reads.
_RESOURCE_PARAMS = ("exam", "node", "goal", "review", "session")


def web_base_url() -> str | None:
    """Live host base URL, or None when nothing is known (caller goes relative)."""
    try:
        text = _sidecar_path().read_text(encoding="utf-8").strip()
        if text:
            return text.rstrip("/")
    except (OSError, ValueError):
        pass

    env = os.environ.get("LDG_WEB_URL")
    if env and env.strip():
        return env.strip().rstrip("/")

    return None


def deep_link(view: str, **resources: str | None) -> str:
    """
    Build a deep link to a flow view, optionally targeting a resource id.

    Unknown/None resource kwargs are dropped. When no base URL is known the
    result is the relative ?view=… path so the learner can open it on their
    running host.
    """
    params: list[tuple[str, str]] = [("view", view)]
    for key in _RESOURCE_PARAMS:
        value = resources.get(key)
        if value:
            params.append((key, str(value)))

    query = urlencode(params)
    base = web_base_url()
    if base:
        return f"{base}/?{query}"
    return f"?{query}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a GUI host deep link.")
    parser.add_argument("--view", required=True, choices=["graph", "assess", "learn", "exam", "review"])
    for key in _RESOURCE_PARAMS:
        parser.add_argument(f"--{key}", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(deep_link(args.view, **{k: getattr(args, k) for k in _RESOURCE_PARAMS}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
