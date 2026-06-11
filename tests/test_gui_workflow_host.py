"""
Tests for the serve-learning-graph GUI workflow host foundation.

Purpose:
    Verify that the GUI host exposes a stable JSON API layer on top of the
    existing graph database without moving SQLite access into Node.

Responsibilities:
    - Exercise the Python workflow API adapter with an explicit DB path
    - Assert read-only foundation actions return stable JSON envelopes
    - Assert the Node server exposes /api/health, /api/goals, and /api/graph
    - Preserve existing /graph.json compatibility

What this file does NOT do:
    - Test interactive assessment, learning, exam, or review flows
    - Call real LLM APIs
    - Modify the example database
"""

from __future__ import annotations

import json
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DB = ROOT / "example" / "learning.db"
WORKFLOW_SCRIPT = ROOT / "skills" / "serve-learning-graph" / "scripts" / "workflow_api.py"
SERVER_SCRIPT = ROOT / "skills" / "serve-learning-graph" / "scripts" / "server.js"
EXAMPLE_GOAL_PREFIX = "3e56ce55"
EXAMPLE_TITLE = "从 0 到 10w DAU 的 SEO 学习路径"
NO_PROXY_OPENER = build_opener(ProxyHandler({}))


def _run_workflow(action: str, payload: dict | None = None) -> dict:
    proc = subprocess.run(
        [
            sys.executable,
            str(WORKFLOW_SCRIPT),
            "--db",
            str(EXAMPLE_DB),
            "--action",
            action,
            "--user",
            "default",
        ],
        input=json.dumps(payload or {}, ensure_ascii=False),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _occupied_port() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def _wait_for_server_url(proc: subprocess.Popen[str]) -> str:
    deadline = time.time() + 10
    lines: list[str] = []
    assert proc.stdout is not None
    line_queue: queue.Queue[str] = queue.Queue()

    def _reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            line_queue.put(line)

    threading.Thread(target=_reader, daemon=True).start()
    while time.time() < deadline:
        try:
            line = line_queue.get(timeout=max(0.0, min(0.25, deadline - time.time())))
        except queue.Empty:
            line = ""
        if line:
            lines.append(line)
            marker = "Listening on "
            if marker in line:
                return line.split(marker, 1)[1].strip()
        if proc.poll() is not None:
            break
    raise AssertionError("server did not start:\n" + "".join(lines))


def _get_json(base_url: str, path: str, params: dict | None = None) -> dict:
    suffix = path
    if params:
        suffix += "?" + urlencode(params)
    with NO_PROXY_OPENER.open(f"{base_url}{suffix}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_workflow_api_health_uses_explicit_db_path():
    payload = _run_workflow("health")

    assert payload["ok"] is True
    assert payload["data"]["db_path"] == str(EXAMPLE_DB.resolve())
    assert payload["data"]["user_id"] == "default"
    assert "graph" in payload["data"]["features"]


def test_workflow_api_lists_goals_for_user():
    payload = _run_workflow("goals")

    assert payload["ok"] is True
    goals = payload["data"]["goals"]
    assert any(goal["title"] == EXAMPLE_TITLE for goal in goals)
    assert all(goal["user_id"] == "default" for goal in goals)


def test_workflow_api_exports_graph_for_goal_prefix():
    payload = _run_workflow("graph", {"goal": EXAMPLE_GOAL_PREFIX})

    assert payload["ok"] is True
    graph = payload["data"]["graph"]
    assert graph["goal"]["title"] == EXAMPLE_TITLE
    assert graph["summary"]["node_count"] == 64
    assert graph["summary"]["edge_count"] == 65


def test_workflow_api_unknown_action_returns_json_error():
    payload = _run_workflow("does-not-exist")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "unknown_action"
    assert "does-not-exist" in payload["error"]["message"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_server_exposes_gui_api_endpoints():
    occupied_socket, occupied_port = _occupied_port()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            [
                shutil.which("node") or "node",
                str(SERVER_SCRIPT),
                "--db",
                str(EXAMPLE_DB),
                "--goal",
                EXAMPLE_GOAL_PREFIX,
                "--user",
                "default",
                "--port",
                str(occupied_port),
                "--python",
                sys.executable,
                "--no-open",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        url = _wait_for_server_url(proc)

        health = _get_json(url, "/api/health")
        assert health["ok"] is True
        assert health["data"]["db_path"] == str(EXAMPLE_DB.resolve())

        goals = _get_json(url, "/api/goals", {"user": "default"})
        assert goals["ok"] is True
        assert any(goal["title"] == EXAMPLE_TITLE for goal in goals["data"]["goals"])

        graph = _get_json(url, "/api/graph", {"goal": EXAMPLE_GOAL_PREFIX, "user": "default"})
        assert graph["ok"] is True
        assert graph["data"]["graph"]["summary"]["node_count"] == 64

        legacy_graph = _get_json(url, "/graph.json")
        assert legacy_graph["goal"]["title"] == EXAMPLE_TITLE

        # The server writes its live URL to the sidecar so generation skills can
        # build a deep link back to this page.
        sidecar = ROOT / "data" / ".web_url"
        assert sidecar.exists()
        assert sidecar.read_text(encoding="utf-8").strip() == url
    finally:
        occupied_socket.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# ── Generation actions defer to the tool (agent_required) ───────────────────────

class TestAgentHandoff:
    """Every generation action returns a structured agent_required envelope
    (skill + command + deep_link) instead of attempting an LLM call. The gate
    fires before any DB side effects, so ids need not exist."""

    @pytest.mark.parametrize(
        "action, payload, skill, command_prefix, deep_link_prefix",
        [
            ("assessment_start", {"goal": EXAMPLE_GOAL_PREFIX}, "goal-assess", "/goal-assess", "?view=assess"),
            ("assessment_answer", {"goal": EXAMPLE_GOAL_PREFIX}, "goal-assess", "/goal-assess", "?view=assess"),
            ("learn_prepare", {"node": "abc123"}, "learn-start", "/learn-start", "?view=learn"),
            ("learn_message", {"session_id": "s1"}, "learn-start", "/learn-start", "?view=learn"),
            ("exam_start", {"node": "abc123"}, "exam-start", "/exam-start", "?view=exam"),
            ("exam_answer", {"exam_id": "e1", "question_id": "q1"}, "exam-start", "/exam-start", "?view=exam"),
            ("exam_finish", {"exam_id": "e1"}, "exam-start", "/exam-start", "?view=exam"),
            ("review_finish", {"node": "abc123", "exam_id": "e1"}, "review-start", "/review-start", "?view=review"),
        ],
    )
    def test_generation_action_defers_to_tool(
        self, action, payload, skill, command_prefix, deep_link_prefix
    ):
        result = _run_workflow(action, payload)
        assert result["ok"] is False, result
        err = result["error"]
        assert err["code"] == "agent_required"
        assert err["skill"] == skill
        assert err["command"].startswith(command_prefix)
        assert err["deep_link"].startswith(deep_link_prefix)
        assert err["message"]

    def test_exam_get_reaches_data_plane(self):
        # Not gated: a missing exam surfaces a data-layer error, proving the
        # request reaches the service rather than an agent_required handoff.
        result = _run_workflow("exam_get", {"exam_id": "does-not-exist"})
        assert result["ok"] is False
        assert result["error"]["code"] == "exam_not_found"

    def test_exam_record_answer_reaches_data_plane(self):
        result = _run_workflow(
            "exam_record_answer",
            {"exam_id": "does-not-exist", "question_id": "q1", "user_answer": "x"},
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "exam_not_found"

    def test_review_queue_is_data_plane(self):
        result = _run_workflow("review_queue", {})
        assert result["ok"] is True
