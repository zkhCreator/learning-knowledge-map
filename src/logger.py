"""
File: logger.py

Purpose:
    Centralised logging setup for the learning system.

Design:
    - Always writes DEBUG+ to data/learning.log (file handler)
    - Optionally also writes DEBUG to stderr (console handler) when verbose=True
    - All modules import `get_logger(__name__)` — never configure logging themselves

Log levels in use:
    DEBUG   — raw prompts, raw model responses, token counts, timings
    INFO    — normal progress (node created, agent approved, etc.)
    WARNING — recoverable issues (retry, fallback)
    ERROR   — failures that abort an operation
"""

import logging
import sys
from pathlib import Path

from src import config

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

_file_handler: logging.FileHandler | None = None
_console_handler: logging.StreamHandler | None = None
_verbose: bool = False


def setup(verbose: bool = False):
    """
    Call once at CLI startup.

    Args:
        verbose: If True, also stream DEBUG logs to stderr.
    """
    global _file_handler, _console_handler, _verbose
    _verbose = verbose

    log_path: Path = config.DB_PATH.parent / "learning.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Remove any handlers added by previous setup() calls (e.g. in tests)
    for h in root.handlers[:]:
        root.removeHandler(h)

    # ── File handler (always on) ───────────────────────────────────────────
    _file_handler = logging.FileHandler(log_path, encoding="utf-8")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(_FORMATTER)
    root.addHandler(_file_handler)

    # ── Console handler (verbose only) ────────────────────────────────────
    if verbose:
        _console_handler = logging.StreamHandler(sys.stderr)
        _console_handler.setLevel(logging.DEBUG)
        _console_handler.setFormatter(_FORMATTER)
        root.addHandler(_console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    log = logging.getLogger(__name__)
    log.info("Logging initialised — file: %s  verbose: %s", log_path, verbose)
    if verbose:
        log.debug("Verbose mode ON — all DEBUG logs also printed to stderr")


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger. Call as: log = get_logger(__name__)"""
    return logging.getLogger(name)
