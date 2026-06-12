"""
Tests for the unified DB path resolution rule (docs/20).

Purpose:
    Pin every entrypoint to the same three-tier rule:
        explicit argument > DB_PATH env var > Path.cwd()/"learning.db"
    and pin the stdlib-autonomous skill copies (export_graph / print_graph)
    to the canonical implementation in src/infrastructure/config.py so the
    deliberately duplicated code cannot drift.

Responsibilities:
    - Assert each resolver honours the three-tier priority
    - Assert explicit directory / sqlite-suffix semantics (write entrypoints)
    - Assert exam_cli._set_db_path no longer clobbers an existing DB_PATH env
    - Cross-implementation consistency for identical inputs

What this file does NOT do:
    - Touch real databases (path math only)
    - Exercise goal resolution or graph export logic
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def export_graph_mod():
    return _load_script("skills/serve-learning-graph/scripts/export_graph.py", "t_export_graph")


@pytest.fixture
def print_graph_mod():
    return _load_script("skills/print-learning-graph/scripts/print_graph.py", "t_print_graph")


@pytest.fixture
def persist_mod():
    return _load_script("skills/decompose-learning-goal/scripts/persist_result.py", "t_persist_result")


@pytest.fixture
def exam_cli_mod():
    return _load_script("skills/exam-start/scripts/exam_cli.py", "t_exam_cli")


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    return monkeypatch


# ── Canonical implementation: src/infrastructure/config.py ────────────────────

class TestConfigResolveDbPath:
    def test_default_is_cwd_learning_db(self, tmp_path, clean_env):
        from src.infrastructure import config

        clean_env.chdir(tmp_path)
        assert config.resolve_db_path() == tmp_path / "learning.db"

    def test_env_beats_default(self, tmp_path, clean_env):
        from src.infrastructure import config

        env_db = tmp_path / "env" / "x.db"
        clean_env.setenv("DB_PATH", str(env_db))
        clean_env.chdir(tmp_path)
        assert config.resolve_db_path() == env_db

    def test_explicit_beats_env(self, tmp_path, clean_env):
        from src.infrastructure import config

        clean_env.setenv("DB_PATH", str(tmp_path / "env.db"))
        explicit = tmp_path / "explicit.db"
        assert config.resolve_db_path(explicit) == explicit

    def test_explicit_directory_appends_filename(self, tmp_path, clean_env):
        from src.infrastructure import config

        target_dir = tmp_path / "dbs"
        assert config.resolve_db_path(target_dir) == target_dir / "learning.db"

    def test_explicit_relative_resolves_against_cwd(self, tmp_path, clean_env):
        from src.infrastructure import config

        clean_env.chdir(tmp_path)
        assert config.resolve_db_path("sub/my.sqlite3") == tmp_path / "sub" / "my.sqlite3"


# ── Stdlib-autonomous skill copies ─────────────────────────────────────────────

class TestSkillResolverParity:
    """export_graph / print_graph stay stdlib-only by design (file comments);
    these tests pin their duplicated resolver to the canonical rule."""

    @pytest.fixture(params=["export", "print"])
    def resolver(self, request, export_graph_mod, print_graph_mod):
        mod = export_graph_mod if request.param == "export" else print_graph_mod
        return mod.resolve_db_path

    def test_default_is_cwd_learning_db(self, resolver, tmp_path, clean_env):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        # A .git above must NOT pull the default away from cwd any more.
        (tmp_path / ".git").mkdir()
        assert resolver(None, cwd=nested) == nested / "learning.db"

    def test_env_beats_default(self, resolver, tmp_path, clean_env):
        env_db = tmp_path / "env.db"
        clean_env.setenv("DB_PATH", str(env_db))
        assert resolver(None, cwd=tmp_path) == env_db

    def test_explicit_beats_env(self, resolver, tmp_path, clean_env):
        clean_env.setenv("DB_PATH", str(tmp_path / "env.db"))
        explicit = tmp_path / "explicit.db"
        assert resolver(str(explicit)) == explicit

    def test_matches_canonical_default(self, resolver, tmp_path, clean_env):
        from src.infrastructure import config

        clean_env.chdir(tmp_path)
        assert resolver(None, cwd=tmp_path) == config.resolve_db_path(cwd=tmp_path)


class TestPersistResultResolver:
    def test_default_is_cwd_learning_db(self, persist_mod, tmp_path, clean_env):
        nested = tmp_path / "proj" / "deep"
        nested.mkdir(parents=True)
        (tmp_path / "proj" / ".git").mkdir()
        assert persist_mod.resolve_db_path(None, cwd=nested) == nested / "learning.db"

    def test_env_layer_present(self, persist_mod, tmp_path, clean_env):
        env_db = tmp_path / "env.db"
        clean_env.setenv("DB_PATH", str(env_db))
        assert persist_mod.resolve_db_path(None, cwd=tmp_path) == env_db

    def test_explicit_beats_env(self, persist_mod, tmp_path, clean_env):
        clean_env.setenv("DB_PATH", str(tmp_path / "env.db"))
        db_dir = tmp_path / "db-dir"
        db_file = tmp_path / "custom.sqlite"
        assert persist_mod.resolve_db_path(str(db_dir), cwd=tmp_path) == db_dir / "learning.db"
        assert persist_mod.resolve_db_path(str(db_file), cwd=tmp_path) == db_file


class TestExamCliSetDbPath:
    def test_explicit_wins_and_sets_env(self, exam_cli_mod, tmp_path, clean_env):
        clean_env.setenv("DB_PATH", str(tmp_path / "env.db"))
        explicit = tmp_path / "explicit.db"
        resolved = exam_cli_mod._set_db_path(str(explicit))
        assert Path(resolved) == explicit
        import os

        assert os.environ["DB_PATH"] == str(explicit)

    def test_existing_env_is_preserved(self, exam_cli_mod, tmp_path, clean_env):
        env_db = tmp_path / "env.db"
        clean_env.setenv("DB_PATH", str(env_db))
        resolved = exam_cli_mod._set_db_path(None)
        assert Path(resolved) == env_db
        import os

        assert os.environ["DB_PATH"] == str(env_db)

    def test_default_is_cwd_learning_db(self, exam_cli_mod, tmp_path, clean_env):
        clean_env.chdir(tmp_path)
        resolved = exam_cli_mod._set_db_path(None)
        assert Path(resolved) == tmp_path / "learning.db"


# ── Sidecar follows the DB directory ──────────────────────────────────────────

class TestSidecarFollowsDb:
    def test_web_base_url_reads_sidecar_next_to_db(self, tmp_path, monkeypatch):
        from src.infrastructure import config, web_link

        db_file = tmp_path / "nested" / "learning.db"
        db_file.parent.mkdir(parents=True)
        (db_file.parent / ".web_url").write_text("http://127.0.0.1:9001\n", encoding="utf-8")

        monkeypatch.setattr(config, "DB_PATH", db_file)
        monkeypatch.setattr(web_link, "SIDECAR_PATH", None)
        monkeypatch.delenv("LDG_WEB_URL", raising=False)

        assert web_link.web_base_url() == "http://127.0.0.1:9001"


class TestExplicitDirectoryParity:
    """An explicit directory means <dir>/learning.db at every entrypoint."""

    def test_skill_resolvers_expand_directories(self, export_graph_mod, print_graph_mod,
                                                tmp_path, clean_env):
        target_dir = tmp_path / "dbs"
        target_dir.mkdir()
        for resolver in (export_graph_mod.resolve_db_path, print_graph_mod.resolve_db_path):
            assert resolver(str(target_dir)) == target_dir / "learning.db"

    def test_exam_cli_expands_directories(self, exam_cli_mod, tmp_path, clean_env):
        target_dir = tmp_path / "dbs"
        target_dir.mkdir()
        resolved = exam_cli_mod._set_db_path(str(target_dir))
        assert Path(resolved) == target_dir / "learning.db"

    def test_matches_canonical(self, export_graph_mod, tmp_path, clean_env):
        from src.infrastructure import config

        target_dir = tmp_path / "dbs"
        assert export_graph_mod.resolve_db_path(str(target_dir)) == config.resolve_db_path(target_dir)
