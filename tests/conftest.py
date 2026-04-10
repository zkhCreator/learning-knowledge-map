"""
Shared pytest fixtures for the learning graph engine test suite.

Key fixtures:
    tmp_db      — patches config.DB_PATH to a temp file and initialises schema
    make_goal   — factory helper: create a goal row and return it
    make_node   — factory helper: create a node row and return it
    make_outline— factory helper: create a validated outline row and return it
"""

import json
import pytest


# ── DB fixture ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Redirect all DB operations to a fresh temporary SQLite file.
    Automatically initialises the full schema before yielding.
    """
    db_file = tmp_path / "test_learning.db"
    monkeypatch.setattr("src.config.DB_PATH", db_file)
    # Re-import database so get_connection() picks up the patched path
    from src.db import database as db
    db.init_db()
    yield db_file


# ── Factory helpers ────────────────────────────────────────────────────────────

@pytest.fixture
def make_goal(tmp_db):
    """Return a callable that creates a learning_goal row."""
    from src.db import database as db

    def _factory(title="Test Goal", user_id="default"):
        return db.create_goal(title=title, user_id=user_id)

    return _factory


@pytest.fixture
def make_node(tmp_db):
    """Return a callable that creates a knowledge_node row."""
    from src.db import database as db

    def _factory(
        title="Test Node",
        goal_id=None,
        is_atomic=True,
        depth_level=1,
        strictness_level="standard",
        mastery_threshold=0.80,
        description="A test node",
        domain="testing",
    ):
        if goal_id is None:
            goal = db.create_goal("Auto Goal")
            goal_id = goal["id"]
        return db.create_node(
            title=title,
            goal_id=goal_id,
            is_atomic=is_atomic,
            depth_level=depth_level,
            strictness_level=strictness_level,
            mastery_threshold=mastery_threshold,
            description=description,
            domain=domain,
        )

    return _factory


@pytest.fixture
def make_outline(tmp_db, make_node):
    """Return a callable that creates a validated outline row for a node."""
    from src.db import database as db

    def _factory(node_id=None, sections=None, user_id="default"):
        if node_id is None:
            node_id = make_node()["id"]
        if sections is None:
            sections = [
                {
                    "index": 1,
                    "title": "Section One",
                    "content": "Content of section one.",
                    "needs_search": False,
                    "sources": [],
                    "analogy": None,
                    "analogy_source_node": None,
                    "covered": False,
                },
                {
                    "index": 2,
                    "title": "Section Two",
                    "content": "Content of section two.",
                    "needs_search": False,
                    "sources": [],
                    "analogy": "Like X is to Y",
                    "analogy_source_node": "Domain X",
                    "covered": False,
                },
            ]
        outline = db.create_outline(node_id=node_id, sections=sections, user_id=user_id)
        db.update_outline(outline["id"], status="validated")
        outline["status"] = "validated"
        outline["sections"] = sections
        return outline

    return _factory
