"""
tests/test_db_migration.py

Unit tests for the PRAGMA user_version schema migration mechanism in
src/data/database.py (docs/21). Covers: fresh init stamps the current
version, a legacy v0 database is upgraded on first connection, and
migration is idempotent across repeated opens.
"""

from __future__ import annotations

import sqlite3

import pytest


def _user_version(db_file) -> int:
    with sqlite3.connect(db_file) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _table_names(db_file) -> set[str]:
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


def test_init_db_stamps_schema_version(tmp_db):
    from src.data import database as db

    assert _user_version(tmp_db) == db.SCHEMA_VERSION


def test_legacy_v0_db_upgraded_on_first_connection(tmp_path, monkeypatch):
    """A pre-migration database (user_version 0, missing newer tables) is
    brought up to the current schema the first time any entrypoint opens it."""
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        # Minimal v0 shape: only the original goals table, version 0.
        conn.execute(
            """CREATE TABLE learning_goals (
                   id TEXT PRIMARY KEY,
                   user_id TEXT NOT NULL DEFAULT 'default',
                   title TEXT NOT NULL,
                   root_node TEXT,
                   status TEXT NOT NULL DEFAULT 'decomposing',
                   created_at TEXT NOT NULL
               )"""
        )

    monkeypatch.setattr("src.infrastructure.config.DB_PATH", legacy)
    from src.data import database as db

    db._migrated_paths.discard(str(legacy))
    with db.get_connection() as conn:
        conn.execute("SELECT 1 FROM knowledge_nodes LIMIT 1")  # table now exists

    assert _user_version(legacy) == db.SCHEMA_VERSION
    assert "review_schedule" in _table_names(legacy)
    assert "error_notebook" in _table_names(legacy)


def test_migration_is_idempotent(tmp_path, monkeypatch):
    fresh = tmp_path / "fresh.db"
    monkeypatch.setattr("src.infrastructure.config.DB_PATH", fresh)
    from src.data import database as db

    db._migrated_paths.discard(str(fresh))
    db.init_db()
    first_tables = _table_names(fresh)

    # Re-open from a "new process" (cleared memo) — must not fail or change shape.
    db._migrated_paths.discard(str(fresh))
    with db.get_connection() as conn:
        conn.execute("SELECT 1")
    db.init_db()

    assert _table_names(fresh) == first_tables
    assert _user_version(fresh) == db.SCHEMA_VERSION


def test_v1_db_upgraded_to_v2(tmp_path, monkeypatch):
    """A v1 database (exam_questions without `origin`, no exam_validations)
    gains the column and the table on the next connection (docs/23)."""
    v1 = tmp_path / "v1.db"
    with sqlite3.connect(v1) as conn:
        conn.execute(
            """CREATE TABLE exam_questions (
                   id TEXT PRIMARY KEY,
                   exam_id TEXT NOT NULL,
                   question_type TEXT NOT NULL DEFAULT 'short_answer',
                   question TEXT NOT NULL,
                   options TEXT,
                   expected_answer TEXT NOT NULL,
                   user_answer TEXT,
                   score REAL,
                   source_section INTEGER,
                   is_expansion INTEGER NOT NULL DEFAULT 0,
                   created_at TEXT NOT NULL
               )"""
        )
        conn.execute("PRAGMA user_version = 1")

    monkeypatch.setattr("src.infrastructure.config.DB_PATH", v1)
    from src.data import database as db

    db._migrated_paths.discard(str(v1))
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(exam_questions)")}
        assert "origin" in cols
        conn.execute("SELECT 1 FROM exam_validations LIMIT 1")

    assert _user_version(v1) == db.SCHEMA_VERSION
