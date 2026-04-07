"""
File: cli/entrypoints.py

Purpose:
    Shared bootstrap helpers for the CLI entrypoints.

Responsibilities:
    - Route each filesystem entrypoint to the correct Typer app
    - Convert missing CLI dependency errors into actionable install guidance

What this file does NOT do:
    - Define commands
    - Implement business logic
    - Hide unexpected import/runtime failures
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLI_DEPENDENCIES = {"typer", "rich", "dotenv"}


def _missing_dependency_message(missing_module: str) -> str:
    entrypoint = Path(sys.argv[0]).name if sys.argv else "main.py"
    return (
        f"缺少 CLI 依赖模块：{missing_module}\n"
        "请先安装项目依赖：\n"
        "  python3 -m pip install -r requirements.txt\n"
        f"然后重新运行：python3 {entrypoint} {' '.join(sys.argv[1:])}".rstrip()
        + "\n"
    )


def _run(import_target: str, attr_name: str):
    try:
        module = __import__(import_target, fromlist=[attr_name])
    except ModuleNotFoundError as exc:
        missing_root = (exc.name or "").split(".")[0]
        if missing_root in _CLI_DEPENDENCIES:
            sys.stderr.write(_missing_dependency_message(missing_root))
            raise SystemExit(1) from exc
        raise

    getattr(module, attr_name)()


def run_main():
    """Launch the full CLI app."""
    _run("src.cli.main", "app")


def run_goal():
    """Launch the goal-only compatibility entrypoint."""
    _run("src.cli.main", "goal_app")
