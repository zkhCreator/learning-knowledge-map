"""
File: tests/test_cli_boundary.py

Purpose:
    Lock the CLI / Skill boundary defined in docs/19-cli-skill-boundary.md.
    After收口, the CLI is a pure data plane: it never calls an LLM. The 7
    generation commands are removed from the Typer app; the 11 data commands
    stay discoverable.

Responsibilities:
    - Assert the 7 generation commands are no longer registered (invoking them
      yields a non-zero exit with Typer's "No such command").
    - Assert the 11 data commands are still discoverable (`--help` exits 0).

What this file does NOT do:
    - Exercise the data commands' behavior (covered by their own tests/db tests).
    - Make any real LLM calls.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from src.interface.main import app

runner = CliRunner()


# Commands that moved to skills and must NOT be reachable from the CLI anymore.
REMOVED_COMMANDS = [
    ["goal", "new", "x"],
    ["goal", "assess", "x"],
    ["learn", "start", "x"],
    ["learn", "chat", "x"],
    ["exam", "start", "x"],
    ["errors", "review", "x"],
    ["review", "start", "x"],
]

# Data-plane commands that must stay discoverable (never call an LLM).
KEPT_COMMANDS = [
    ["init"],
    ["status"],
    ["goal", "list"],
    ["goal", "remove"],
    ["goal", "export"],
    ["goal", "tree"],
    ["goal", "nodes"],
    ["learn", "progress"],
    ["exam", "review"],
    ["errors", "list"],
    ["review", "list"],
]


@pytest.mark.parametrize("args", REMOVED_COMMANDS, ids=lambda a: " ".join(a))
def test_generation_command_is_removed(args):
    """A removed generation command is not registered: Typer rejects it."""
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert "No such command" in result.output


@pytest.mark.parametrize("args", KEPT_COMMANDS, ids=lambda a: " ".join(a))
def test_data_command_is_discoverable(args):
    """A kept data command is still registered: --help exits cleanly."""
    result = runner.invoke(app, args + ["--help"])
    assert result.exit_code == 0
