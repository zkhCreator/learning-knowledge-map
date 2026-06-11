"""
Tests for decompose-learning-goal skill persistence.

Purpose:
    Verify that the skill's machine-readable JSON can be deterministically
    persisted into a SQLite database compatible with the existing CLI data
    model.

Responsibilities:
    - Assert database path resolution follows skill execution rules
    - Assert the persistence script creates the CLI graph tables and indexes
    - Assert skill JSON maps into learning_goals, knowledge_nodes, and
      knowledge_edges without model-generated SQL
    - Assert import metadata is stored separately from the CLI graph tables
    - Assert duplicate imports are idempotent and invalid title references fail

What this file does NOT do:
    - Execute the learning-goal decomposition skill
    - Run subagent review
    - Exercise the interactive CLI
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "skills" / "decompose-learning-goal" / "scripts" / "persist_result.py"


def _load_persist_module():
    spec = importlib.util.spec_from_file_location("decompose_skill_persist_result", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_result() -> dict:
    return {
        "target": "Learn Kubernetes service networking",
        "assumptions": ["Learner already understands basic TCP/IP."],
        "sources": [
            {
                "title": "Kubernetes Services",
                "url": "https://kubernetes.io/docs/concepts/services-networking/service/",
                "used_for": "Service networking terminology",
            }
        ],
        "nodes": [
            {
                "title": "Kubernetes service networking",
                "description": "Understand stable Service access to changing Pod backends.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["stable abstraction", "dynamic membership"],
                "difficulty": 3,
                "est_minutes": 0,
                "strictness_level": "standard",
                "mastery_threshold": 0.8,
                "risk_note": "",
                "is_atomic": False,
                "parent_title": None,
                "qa_draft": [],
            },
            {
                "title": "ClusterIP service model",
                "description": "Explain stable virtual IPs for Services.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["stable abstraction", "indirection"],
                "difficulty": 2,
                "est_minutes": 12,
                "strictness_level": "standard",
                "mastery_threshold": 0.8,
                "risk_note": "",
                "is_atomic": True,
                "parent_title": "Kubernetes service networking",
                "qa_draft": [
                    "What problem does ClusterIP solve?",
                    "Why can Pods change without client address changes?",
                    "How is a Service IP different from a Pod IP?",
                ],
            },
            {
                "title": "EndpointSlice mapping",
                "description": "Explain how EndpointSlices represent Service backends.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["backend registry", "dynamic membership"],
                "difficulty": 3,
                "est_minutes": 15,
                "strictness_level": "critical",
                "mastery_threshold": 0.95,
                "risk_note": "This bridge prevents confusing stable Services with mutable Pods.",
                "is_atomic": True,
                "parent_title": "Kubernetes service networking",
                "qa_draft": [
                    "What information does an EndpointSlice store?",
                    "When does EndpointSlice membership change?",
                    "Why is EndpointSlice separate from ClusterIP?",
                ],
            },
        ],
        "edges": [
            {
                "from_title": "ClusterIP service model",
                "to_title": "EndpointSlice mapping",
                "edge_type": "prerequisite",
                "weight": 1.0,
                "analogy_desc": None,
            }
        ],
        "review": {
            "status": "approved",
            "provider": "codex",
            "local_checks": [
                {
                    "parent_title": "Kubernetes service networking",
                    "status": "approved",
                    "provider": "codex",
                    "issues": [],
                    "suggestions_applied": [],
                }
            ],
            "global_check": {
                "status": "approved",
                "provider": "codex",
                "issues": [],
                "suggestions_applied": [],
            },
            "issues": [],
            "suggestions_applied": [],
        },
        "unresolved_questions": [],
    }


def _fetch_all(db_path: Path, sql: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql).fetchall()]


def test_resolve_db_path_defaults_to_current_project_root(tmp_path):
    persist = _load_persist_module()
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / ".git").mkdir()

    assert persist.resolve_db_path(None, cwd=nested) == project / "learning.db"


def test_resolve_db_path_accepts_directory_or_sqlite_file(tmp_path):
    persist = _load_persist_module()
    db_dir = tmp_path / "db-dir"
    db_file = tmp_path / "custom.sqlite"

    assert persist.resolve_db_path(str(db_dir), cwd=tmp_path) == db_dir / "learning.db"
    assert persist.resolve_db_path(str(db_file), cwd=tmp_path) == db_file


def test_load_result_accepts_markdown_fenced_json():
    persist = _load_persist_module()
    data = _sample_result()
    text = "Human summary\n\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"

    assert persist.load_result_text(text)["target"] == data["target"]


def test_persist_result_creates_cli_schema_indexes_and_skill_metadata(tmp_path):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"

    summary = persist.persist_result(_sample_result(), db_path=db_path, user_id="alice")

    assert summary["db_path"] == str(db_path)
    assert summary["imported"] is True
    assert summary["nodes"] == 3
    assert summary["edges"] == 1

    tables = {
        row["name"]
        for row in _fetch_all(
            db_path,
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        )
    }
    assert "learning_goals" in tables
    assert "knowledge_nodes" in tables
    assert "knowledge_edges" in tables
    assert "skill_result_imports" in tables
    assert "skill_result_sources" in tables

    indexes = {
        row["name"]
        for row in _fetch_all(
            db_path,
            "SELECT name FROM sqlite_master WHERE type = 'index'",
        )
    }
    assert "idx_nodes_goal" in indexes
    assert "idx_edges_from" in indexes
    assert "idx_edges_to" in indexes
    assert "idx_skill_import_hash" in indexes
    assert "idx_skill_source_url" in indexes


def test_persist_result_maps_skill_json_to_cli_graph_tables(tmp_path):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"

    summary = persist.persist_result(_sample_result(), db_path=db_path, user_id="alice")

    goals = _fetch_all(db_path, "SELECT * FROM learning_goals")
    assert len(goals) == 1
    assert goals[0]["id"] == summary["goal_id"]
    assert goals[0]["user_id"] == "alice"
    assert goals[0]["title"] == "Learn Kubernetes service networking"
    assert goals[0]["status"] == "active"
    assert goals[0]["root_node"]

    nodes = _fetch_all(db_path, "SELECT * FROM knowledge_nodes ORDER BY depth_level, title")
    assert len(nodes) == 3
    root = next(node for node in nodes if node["title"] == "Kubernetes service networking")
    child = next(node for node in nodes if node["title"] == "EndpointSlice mapping")
    assert root["id"] == goals[0]["root_node"]
    assert root["parent_node"] is None
    assert child["parent_node"] == root["id"]
    assert child["goal_id"] == goals[0]["id"]
    assert child["is_atomic"] == 1
    assert child["strictness_level"] == "critical"
    assert child["mastery_threshold"] == 0.95

    qa_set = json.loads(child["qa_set"])
    assert qa_set == [
        {
            "question": "What information does an EndpointSlice store?",
            "expected_answer": "",
            "difficulty": 3,
        },
        {
            "question": "When does EndpointSlice membership change?",
            "expected_answer": "",
            "difficulty": 3,
        },
        {
            "question": "Why is EndpointSlice separate from ClusterIP?",
            "expected_answer": "",
            "difficulty": 3,
        },
    ]

    edges = _fetch_all(db_path, "SELECT * FROM knowledge_edges")
    assert len(edges) == 1
    title_by_id = {node["id"]: node["title"] for node in nodes}
    assert title_by_id[edges[0]["from_node"]] == "ClusterIP service model"
    assert title_by_id[edges[0]["to_node"]] == "EndpointSlice mapping"

    imports = _fetch_all(db_path, "SELECT * FROM skill_result_imports")
    assert len(imports) == 1
    assert imports[0]["goal_id"] == goals[0]["id"]
    assert imports[0]["review_status"] == "approved"
    assert imports[0]["review_provider"] == "codex"
    assert json.loads(imports[0]["assumptions"]) == ["Learner already understands basic TCP/IP."]

    sources = _fetch_all(db_path, "SELECT * FROM skill_result_sources")
    assert len(sources) == 1
    assert sources[0]["url"].startswith("https://kubernetes.io/")


def test_persist_result_is_idempotent_by_content_hash(tmp_path):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"
    data = _sample_result()

    first = persist.persist_result(data, db_path=db_path, user_id="alice")
    second = persist.persist_result(data, db_path=db_path, user_id="alice")

    assert first["import_id"] == second["import_id"]
    assert first["goal_id"] == second["goal_id"]
    assert first["imported"] is True
    assert second["imported"] is False
    assert len(_fetch_all(db_path, "SELECT * FROM learning_goals")) == 1
    assert len(_fetch_all(db_path, "SELECT * FROM knowledge_nodes")) == 3
    assert len(_fetch_all(db_path, "SELECT * FROM knowledge_edges")) == 1


def test_persist_result_is_idempotent_per_user(tmp_path):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"
    data = _sample_result()

    alice = persist.persist_result(data, db_path=db_path, user_id="alice")
    bob = persist.persist_result(data, db_path=db_path, user_id="bob")
    bob_again = persist.persist_result(data, db_path=db_path, user_id="bob")

    assert alice["imported"] is True
    assert bob["imported"] is True
    assert bob_again["imported"] is False
    assert alice["goal_id"] != bob["goal_id"]
    assert bob["goal_id"] == bob_again["goal_id"]
    goals = _fetch_all(db_path, "SELECT user_id, id FROM learning_goals ORDER BY user_id")
    assert [goal["user_id"] for goal in goals] == ["alice", "bob"]
    assert len(_fetch_all(db_path, "SELECT * FROM skill_result_imports")) == 2


def test_import_metadata_does_not_block_cli_goal_delete(tmp_path, monkeypatch):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"
    summary = persist.persist_result(_sample_result(), db_path=db_path, user_id="alice")

    monkeypatch.setattr("src.infrastructure.config.DB_PATH", db_path)
    from src.data import database as db

    deleted = db.delete_goal(summary["goal_id"])

    assert deleted["goals"] == 1
    assert _fetch_all(db_path, "SELECT * FROM skill_result_imports") == []
    assert _fetch_all(db_path, "SELECT * FROM skill_result_sources") == []


def test_persist_result_rejects_unknown_edge_type(tmp_path):
    persist = _load_persist_module()
    data = _sample_result()
    data["edges"][0]["edge_type"] = "depends_on"

    with pytest.raises(ValueError, match="edge_type"):
        persist.persist_result(data, db_path=tmp_path / "learning.db", user_id="alice")


def test_persist_result_rejects_prerequisite_cycle_between_atomic_nodes(tmp_path):
    persist = _load_persist_module()
    data = _sample_result()
    data["edges"].append(
        {
            "from_title": "EndpointSlice mapping",
            "to_title": "ClusterIP service model",
            "edge_type": "prerequisite",
            "weight": 1.0,
            "analogy_desc": None,
        }
    )

    with pytest.raises(ValueError, match="cycle"):
        persist.persist_result(data, db_path=tmp_path / "learning.db", user_id="alice")


def test_persist_result_rejects_prerequisite_edge_with_non_atomic_endpoint(tmp_path):
    persist = _load_persist_module()
    data = _sample_result()
    data["edges"].append(
        {
            "from_title": "Kubernetes service networking",
            "to_title": "ClusterIP service model",
            "edge_type": "prerequisite",
            "weight": 1.0,
            "analogy_desc": None,
        }
    )

    with pytest.raises(ValueError, match="atomic"):
        persist.persist_result(data, db_path=tmp_path / "learning.db", user_id="alice")


def test_persist_result_rejects_unknown_edge_title_without_partial_rows(tmp_path):
    persist = _load_persist_module()
    db_path = tmp_path / "learning.db"
    data = _sample_result()
    data["edges"][0]["from_title"] = "Missing node"

    with pytest.raises(ValueError, match="Missing node"):
        persist.persist_result(data, db_path=db_path, user_id="alice")

    assert _fetch_all(db_path, "SELECT * FROM learning_goals") == []
    assert _fetch_all(db_path, "SELECT * FROM knowledge_nodes") == []
    assert _fetch_all(db_path, "SELECT * FROM knowledge_edges") == []
