"""
Tests for learning graph render skills.

Purpose:
    Verify that the print and Node-server skills can read an existing
    Learning Directed Graph SQLite database and render the parent-child
    knowledge hierarchy without modifying data.

Responsibilities:
    - Exercise real example DB data as an integration fixture
    - Assert goal prefix resolution and parent-child tree rendering
    - Assert server export JSON contains hierarchy and prerequisite edges
    - Assert the Node server skips an occupied port and serves HTML/JSON

What this file does NOT do:
    - Test learning goal decomposition
    - Test browser UI interaction
    - Modify the example database
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import sqlite3
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DB = ROOT / "example" / "learning.db"
PRINT_SCRIPT = ROOT / "skills" / "print-learning-graph" / "scripts" / "print_graph.py"
EXPORT_SCRIPT = ROOT / "skills" / "serve-learning-graph" / "scripts" / "export_graph.py"
SERVER_SCRIPT = ROOT / "skills" / "serve-learning-graph" / "scripts" / "server.js"
EXAMPLE_GOAL_PREFIX = "3e56ce55"
EXAMPLE_TITLE = "从 0 到 10w DAU 的 SEO 学习路径"
NO_PROXY_OPENER = build_opener(ProxyHandler({}))


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_example_db():
    assert EXAMPLE_DB.exists(), f"missing example fixture: {EXAMPLE_DB}"


def _create_edge_case_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "edge_cases.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE learning_goals (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                root_node TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_nodes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                domain TEXT,
                concept_fingerprint TEXT,
                difficulty INTEGER,
                est_minutes INTEGER,
                qa_set TEXT,
                depth_level INTEGER,
                parent_node TEXT,
                goal_id TEXT,
                strictness_level TEXT,
                mastery_threshold REAL,
                risk_note TEXT,
                is_atomic INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_edges (
                id TEXT PRIMARY KEY,
                from_node TEXT NOT NULL,
                to_node TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                weight REAL,
                analogy_desc TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE user_knowledge_state (
                user_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                raw_score REAL,
                stability REAL,
                last_reviewed TEXT,
                next_review TEXT,
                review_count INTEGER,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = "2026-06-09T00:00:00+00:00"
        conn.execute(
            "INSERT INTO learning_goals VALUES (?,?,?,?,?,?)",
            ("goal-edge", "default", "Edge Case Goal", "root", "active", now),
        )
        nodes = [
            ("root", "Root Node", None, 0, 0),
            ("child", "Child Node", "root", 1, 1),
            ("prereq", "Prerequisite Node", "root", 1, 1),
            ("analogy", "Analogy Peer", "root", 1, 1),
            ("orphan", "Detached Node", "missing-parent", 2, 1),
        ]
        for node_id, title, parent, depth, is_atomic in nodes:
            conn.execute(
                """INSERT INTO knowledge_nodes VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (
                    node_id,
                    title,
                    "",
                    "testing",
                    "[]",
                    1,
                    5,
                    "[]",
                    depth,
                    parent,
                    "goal-edge",
                    "standard",
                    0.8,
                    "",
                    is_atomic,
                    now,
                ),
            )
        conn.execute(
            "INSERT INTO knowledge_edges VALUES (?,?,?,?,?,?,?)",
            ("edge-prereq", "prereq", "child", "prerequisite", 1.0, None, now),
        )
        conn.execute(
            "INSERT INTO knowledge_edges VALUES (?,?,?,?,?,?,?)",
            (
                "edge-analogy",
                "analogy",
                "child",
                "cross_domain_analogy",
                1.0,
                "Shared structure",
                now,
            ),
        )
    return db_path


def test_print_skill_renders_example_parent_tree():
    _require_example_db()
    print_graph = _load_module(PRINT_SCRIPT, "print_learning_graph_script")

    output = print_graph.render_tree(
        db_path=EXAMPLE_DB,
        goal_prefix=EXAMPLE_GOAL_PREFIX,
        user_id="default",
    )

    assert EXAMPLE_TITLE in output
    assert "SEO 目标与搜索系统基础" in output
    assert "使用 site: 快速检查公开收录" in output
    assert "64 nodes" in output


def test_print_skill_missing_goal_raises_clear_error():
    _require_example_db()
    print_graph = _load_module(PRINT_SCRIPT, "print_learning_graph_script_missing")

    with pytest.raises(ValueError, match="No goal"):
        print_graph.render_tree(
            db_path=EXAMPLE_DB,
            goal_prefix="does-not-exist",
            user_id="default",
        )


def test_export_skill_returns_example_payload():
    _require_example_db()
    export_graph = _load_module(EXPORT_SCRIPT, "serve_learning_graph_export")

    payload = export_graph.export_graph(
        db_path=EXAMPLE_DB,
        goal_prefix=EXAMPLE_GOAL_PREFIX,
        user_id="default",
    )

    assert payload["goal"]["title"] == EXAMPLE_TITLE
    assert payload["summary"]["node_count"] == 64
    assert payload["summary"]["edge_count"] == 65
    assert payload["tree"]["title"] == EXAMPLE_TITLE
    assert any(
        child["title"] == "SEO 目标与搜索系统基础"
        for child in payload["tree"]["children"]
    )
    assert any(
        edge["edge_type"] == "prerequisite"
        for edge in payload["edges"]
    )


def test_export_skill_missing_goal_raises_clear_error():
    _require_example_db()
    export_graph = _load_module(EXPORT_SCRIPT, "serve_learning_graph_export_missing")

    with pytest.raises(ValueError, match="No goal"):
        export_graph.export_graph(
            db_path=EXAMPLE_DB,
            goal_prefix="does-not-exist",
            user_id="default",
        )


def test_print_skill_distinguishes_analogy_edges_and_shows_detached_nodes(tmp_path):
    db_path = _create_edge_case_db(tmp_path)
    print_graph = _load_module(PRINT_SCRIPT, "print_learning_graph_script_edges")

    output = print_graph.render_tree(
        db_path=db_path,
        goal_prefix="goal-edge",
        user_id="default",
    )

    assert "prereq: Prerequisite Node" in output
    assert "analogy: Analogy Peer" in output
    assert "prereq: Analogy Peer" not in output
    assert "Unattached nodes:" in output
    assert "Detached Node" in output


def test_export_skill_distinguishes_analogy_edges_and_keeps_unattached_trees(tmp_path):
    db_path = _create_edge_case_db(tmp_path)
    export_graph = _load_module(EXPORT_SCRIPT, "serve_learning_graph_export_edges")

    payload = export_graph.export_graph(
        db_path=db_path,
        goal_prefix="goal-edge",
        user_id="default",
    )

    child = next(node for node in payload["nodes"] if node["id"] == "child")
    assert [item["title"] for item in child["prerequisites"]] == ["Prerequisite Node"]
    assert [item["title"] for item in child["analogies"]] == ["Analogy Peer"]
    assert child["analogies"][0]["analogy_desc"] == "Shared structure"
    assert payload["unattached_trees"][0]["title"] == "Detached Node"
    assert any(tree["title"] == "Detached Node" for tree in payload["trees"])


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_server_serves_html_json_and_skips_occupied_port():
    _require_example_db()
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
                "--no-open",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        url = _wait_for_server_url(proc)
        actual_port = int(url.rsplit(":", 1)[1])
        assert actual_port != occupied_port

        html = NO_PROXY_OPENER.open(url, timeout=5).read().decode("utf-8")
        assert 'id="root"' in html
        asset_match = re.search(r'src="([^"]*/assets/[^"]+\.js)"', html)
        assert asset_match, html
        asset_url = f"{url}{asset_match.group(1)}"
        with NO_PROXY_OPENER.open(asset_url, timeout=5) as response:
            asset = response.read().decode("utf-8")
        assert "React" in asset or "react" in asset

        with NO_PROXY_OPENER.open(f"{url}/graph.json", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["summary"]["node_count"] == 64
        assert payload["goal"]["title"] == EXAMPLE_TITLE

        with pytest.raises(HTTPError) as exc_info:
            NO_PROXY_OPENER.open(f"{url}/missing", timeout=5)
        assert exc_info.value.code == 404
    finally:
        occupied_socket.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_server_survives_browser_open_failure():
    _require_example_db()
    node_bin = shutil.which("node") or "node"
    occupied_socket, occupied_port = _occupied_port()
    proc: subprocess.Popen[str] | None = None
    try:
        env = os.environ.copy()
        env["PATH"] = ""
        proc = subprocess.Popen(
            [
                node_bin,
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
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )

        url = _wait_for_server_url(proc)
        html = NO_PROXY_OPENER.open(url, timeout=5).read().decode("utf-8")
        assert 'id="root"' in html
        time.sleep(0.2)
        assert proc.poll() is None
    finally:
        occupied_socket.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
