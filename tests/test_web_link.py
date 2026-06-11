"""
Tests for src/infrastructure/web_link.py — the GUI host deep-link builder used by generation
skills to point the learner back to the running web data plane.

No server is started; the sidecar file and LDG_WEB_URL env are exercised directly.
"""

from __future__ import annotations

import importlib


def _fresh_module():
    import src.infrastructure.web_link as web_link
    return importlib.reload(web_link)


class TestWebBaseUrl:
    def test_reads_sidecar_first(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        sidecar = tmp_path / ".web_url"
        sidecar.write_text("http://127.0.0.1:8770\n", encoding="utf-8")
        monkeypatch.setattr(web_link, "SIDECAR_PATH", sidecar)
        monkeypatch.setenv("LDG_WEB_URL", "http://env-should-not-win:1")
        assert web_link.web_base_url() == "http://127.0.0.1:8770"

    def test_falls_back_to_env(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        monkeypatch.setattr(web_link, "SIDECAR_PATH", tmp_path / "missing")
        monkeypatch.setenv("LDG_WEB_URL", "http://localhost:9999/")
        assert web_link.web_base_url() == "http://localhost:9999"

    def test_none_when_nothing_known(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        monkeypatch.setattr(web_link, "SIDECAR_PATH", tmp_path / "missing")
        monkeypatch.delenv("LDG_WEB_URL", raising=False)
        assert web_link.web_base_url() is None


class TestDeepLink:
    def test_absolute_when_base_known(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        monkeypatch.setattr(web_link, "SIDECAR_PATH", tmp_path / "missing")
        monkeypatch.setenv("LDG_WEB_URL", "http://localhost:8765")
        link = web_link.deep_link("exam", exam="E1")
        assert link == "http://localhost:8765/?view=exam&exam=E1"

    def test_relative_when_no_base(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        monkeypatch.setattr(web_link, "SIDECAR_PATH", tmp_path / "missing")
        monkeypatch.delenv("LDG_WEB_URL", raising=False)
        assert web_link.deep_link("learn", node="n1") == "?view=learn&node=n1"

    def test_drops_empty_resources(self, tmp_path, monkeypatch):
        web_link = _fresh_module()
        monkeypatch.setattr(web_link, "SIDECAR_PATH", tmp_path / "missing")
        monkeypatch.delenv("LDG_WEB_URL", raising=False)
        link = web_link.deep_link("review", node=None, review="", goal="g1")
        assert link == "?view=review&goal=g1"
