"""
File: skills/*/scripts/_bootstrap.py

Purpose:
    Make the shared Python runtime (the `src` package) importable for skill
    scripts in both deployment modes (docs/22):
      1. installed skill — `npx skills add` copied only this skill directory;
         the runtime lives in the sibling `_core/` vendored by
         scripts/build_skills.py
      2. repo development — no `_core/` needed; the runtime is the repository
         root three levels up

What this file does NOT do:
    - Import any project module itself (it only adjusts sys.path)
    - Resolve database paths or read configuration

Usage (from a sibling script, after putting its own directory on sys.path):
    import _bootstrap  # noqa: F401
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()

_CANDIDATES = [
    _HERE.parents[1] / "_core",  # installed: <skill>/_core/src/
    _HERE.parents[3],            # development: <repo>/src/
]


def _runtime_root() -> Path:
    for candidate in _CANDIDATES:
        if (candidate / "src" / "__init__.py").exists():
            return candidate
    raise ImportError(
        "Cannot locate the learning-graph runtime: neither a vendored _core/ "
        "next to this skill nor a repository root with src/ was found. "
        "Re-run `python scripts/build_skills.py` or reinstall the skill."
    )


_root = str(_runtime_root())
if _root not in sys.path:
    sys.path.insert(0, _root)
