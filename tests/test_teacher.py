"""
tests/test_teacher.py

Unit tests for src/agents/teacher.py.
All LLM calls (llm.call_json / llm.call) are mocked — no real API calls.
WebSearch is also mocked / skipped via env patching.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures / Helpers ─────────────────────────────────────────────────────────

def _sections(n=2):
    return [
        {
            "index": i,
            "title": f"Section {i}",
            "content": f"Content {i}",
            "needs_search": False,
            "sources": [],
            "analogy": None,
            "analogy_source_node": None,
            "covered": False,
        }
        for i in range(1, n + 1)
    ]


OUTLINE_RESPONSE = {"sections": _sections(3)}
APPROVED_REVIEW  = {"approved": True, "issues": [], "corrections": {}}
REJECTED_REVIEW  = {
    "approved": False,
    "issues": ["Section 1 content is inaccurate"],
    "corrections": {"1": "Fix the description of concept X."},
}


# ── generate_outline ──────────────────────────────────────────────────────────

class TestGenerateOutline:
    @patch("src.agents.teacher.llm.call_json")
    def test_normal_flow_creates_validated_outline(self, mock_call, tmp_db, make_node):
        mock_call.side_effect = [OUTLINE_RESPONSE, APPROVED_REVIEW]
        from src.agents.teacher import generate_outline
        from src.db import database as db

        node = make_node()
        outline = generate_outline(node["id"])

        assert outline["status"] == "validated"
        assert len(outline["sections"]) == 3
        # Persisted in DB
        db_outline = db.get_outline(node["id"])
        assert db_outline is not None

    @patch("src.agents.teacher.llm.call_json")
    def test_reuses_existing_validated_outline(self, mock_call, tmp_db, make_node, make_outline):
        """If a validated outline already exists, no LLM call is made."""
        node = make_node()
        existing = make_outline(node_id=node["id"])

        from src.agents.teacher import generate_outline
        result = generate_outline(node["id"])

        mock_call.assert_not_called()
        assert result["id"] == existing["id"]

    @patch("src.agents.teacher.llm.call_json")
    def test_force_regenerate_ignores_existing(self, mock_call, tmp_db, make_node, make_outline):
        mock_call.side_effect = [OUTLINE_RESPONSE, APPROVED_REVIEW]
        node = make_node()
        make_outline(node_id=node["id"])

        from src.agents.teacher import generate_outline
        result = generate_outline(node["id"], force_regenerate=True)

        assert mock_call.call_count == 2  # forward + reverse

    @patch("src.agents.teacher.llm.call_json")
    def test_reverse_reject_adds_correction_note(self, mock_call, tmp_db, make_node):
        mock_call.side_effect = [OUTLINE_RESPONSE, REJECTED_REVIEW]
        from src.agents.teacher import generate_outline

        node = make_node()
        outline = generate_outline(node["id"])

        # Outline is still saved even when rejected
        assert outline["status"] == "validated"
        # correction_note added to section 1
        sec1 = next(s for s in outline["sections"] if s["index"] == 1)
        assert "correction_note" in sec1

    @patch("src.agents.teacher.llm.call_json")
    def test_forward_returns_empty_sections_raises(self, mock_call, tmp_db, make_node):
        mock_call.return_value = {"sections": []}
        from src.agents.teacher import generate_outline

        node = make_node()
        with pytest.raises(ValueError, match="empty"):
            generate_outline(node["id"])

    @patch("src.agents.teacher.llm.call_json")
    def test_missing_node_raises(self, mock_call, tmp_db):
        from src.agents.teacher import generate_outline
        with pytest.raises(ValueError, match="Node not found"):
            generate_outline("nonexistent-id")

    @patch("src.agents.teacher.llm.call_json")
    def test_websearch_skipped_when_not_configured(self, mock_call, tmp_db, make_node, monkeypatch):
        """Even if sections have needs_search=True, search is skipped without API key."""
        sections_with_search = [
            {**s, "needs_search": True}
            for s in _sections(2)
        ]
        mock_call.side_effect = [
            {"sections": sections_with_search},
            APPROVED_REVIEW,
        ]
        monkeypatch.delenv("SEARCH_API_KEY", raising=False)
        monkeypatch.delenv("SEARCH_ENGINE_ID", raising=False)

        from src.agents.teacher import generate_outline
        node = make_node()
        outline = generate_outline(node["id"])
        # Sources remain empty (search was skipped)
        assert all(s["sources"] == [] for s in outline["sections"])

    @patch("src.agents.teacher._websearch")
    @patch("src.agents.teacher.llm.call_json")
    def test_websearch_called_when_configured(
        self, mock_call, mock_search, tmp_db, make_node, monkeypatch
    ):
        sections_with_search = [
            {**s, "needs_search": True}
            for s in _sections(1)
        ]
        mock_call.side_effect = [
            {"sections": sections_with_search},
            APPROVED_REVIEW,
        ]
        mock_search.return_value = [{"title": "Paper", "url": "http://x.com", "snippet": "..."}]
        monkeypatch.setenv("SEARCH_API_KEY", "fake-key")
        monkeypatch.setenv("SEARCH_ENGINE_ID", "fake-cx")

        from src.agents.teacher import generate_outline
        node = make_node()
        generate_outline(node["id"])
        mock_search.assert_called_once()


# ── chat_turn ─────────────────────────────────────────────────────────────────

class TestChatTurn:
    def _make_session(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        db.update_outline(outline["id"], status="active")
        session = db.create_learning_session(node["id"], outline["id"])
        return node, outline, session

    @patch("src.agents.teacher.llm.call_json")
    def test_normal_response_updates_coverage(self, mock_call, tmp_db, make_node, make_outline):
        mock_call.return_value = {
            "response": "Great question! Think about X...",
            "newly_covered_sections": [1],
        }
        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        from src.agents.teacher import chat_turn

        response, progress, covered = chat_turn(
            session=session,
            node=node,
            outline_sections=outline["sections"],
            user_message="What is X?",
            history=[],
        )

        assert response == "Great question! Think about X..."
        assert 1 in covered
        assert progress == pytest.approx(0.5)  # 1 of 2 sections covered

    @patch("src.agents.teacher.llm.call_json")
    def test_coverage_accumulates_across_turns(self, mock_call, tmp_db, make_node, make_outline):
        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        from src.agents.teacher import chat_turn

        # Turn 1: covers section 1
        mock_call.return_value = {"response": "...", "newly_covered_sections": [1]}
        _, _, covered = chat_turn(session, node, outline["sections"], "msg1", [])
        assert covered == [1]

        # Turn 2: covers section 2 (session already has [1])
        mock_call.return_value = {"response": "...", "newly_covered_sections": [2]}
        _, progress, covered = chat_turn(session, node, outline["sections"], "msg2", [])
        assert 1 in covered
        assert 2 in covered
        assert progress == 1.0

    @patch("src.agents.teacher.llm.call")
    @patch("src.agents.teacher.llm.call_json")
    def test_json_parse_failure_falls_back_to_plain_text(
        self, mock_call_json, mock_call, tmp_db, make_node, make_outline
    ):
        mock_call_json.side_effect = ValueError("bad json")
        mock_call.return_value = "Fallback plain text response."

        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        from src.agents.teacher import chat_turn

        response, progress, covered = chat_turn(
            session, node, outline["sections"], "question", []
        )

        assert response == "Fallback plain text response."
        assert covered == []  # no coverage update on fallback
        assert progress == 0.0

    @patch("src.agents.teacher.llm.call_json")
    def test_no_duplicate_sections_in_covered(self, mock_call, tmp_db, make_node, make_outline):
        """Section already in covered_sections is not duplicated."""
        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        from src.db import database as db
        # Pre-set session as already covering section 1
        db.update_session(session["id"], covered_sections=[1], progress=0.5)
        session["covered_sections"] = [1]

        mock_call.return_value = {"response": "...", "newly_covered_sections": [1]}
        from src.agents.teacher import chat_turn
        _, _, covered = chat_turn(session, node, outline["sections"], "msg", [])
        assert covered.count(1) == 1

    @patch("src.agents.teacher.llm.call_json")
    def test_progress_capped_at_1_0(self, mock_call, tmp_db, make_node, make_outline):
        """Covering more sections than total does not push progress above 1.0."""
        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        # Outline has 2 sections; claim 3 newly covered
        mock_call.return_value = {"response": "...", "newly_covered_sections": [1, 2, 3]}
        from src.agents.teacher import chat_turn
        _, progress, _ = chat_turn(session, node, outline["sections"], "msg", [])
        assert progress <= 1.0

    @patch("src.agents.teacher.llm.call_json")
    def test_chat_history_persisted(self, mock_call, tmp_db, make_node, make_outline):
        mock_call.return_value = {"response": "My response", "newly_covered_sections": []}
        node, outline, session = self._make_session(tmp_db, make_node, make_outline)
        from src.agents.teacher import chat_turn
        from src.db import database as db

        chat_turn(session, node, outline["sections"], "User input", [])
        history = db.get_chat_history(session["id"])
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "User input"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "My response"


# ── start_or_resume_session ────────────────────────────────────────────────────

class TestStartOrResumeSession:
    def test_creates_new_session(self, tmp_db, make_node, make_outline):
        node = make_node()
        outline = make_outline(node_id=node["id"])
        from src.agents.teacher import start_or_resume_session
        session = start_or_resume_session(node["id"], outline["id"])
        assert session["node_id"] == node["id"]
        assert session["progress"] == 0.0

    def test_resumes_existing_active_session(self, tmp_db, make_node, make_outline):
        from src.db import database as db
        node = make_node()
        outline = make_outline(node_id=node["id"])
        first = db.create_learning_session(node["id"], outline["id"])

        from src.agents.teacher import start_or_resume_session
        resumed = start_or_resume_session(node["id"], outline["id"])
        assert resumed["id"] == first["id"]


# ── _format_outline_for_prompt ────────────────────────────────────────────────

class TestFormatOutlineForPrompt:
    def test_includes_section_titles(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = _sections(2)
        text = _format_outline_for_prompt(sections)
        assert "Section 1" in text
        assert "Section 2" in text

    def test_covered_section_marked(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = _sections(2)
        sections[0]["covered"] = True
        text = _format_outline_for_prompt(sections)
        assert "✓" in text

    def test_analogy_included_when_present(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = _sections(1)
        sections[0]["analogy"] = "Like a queue in real life"
        text = _format_outline_for_prompt(sections)
        assert "Like a queue in real life" in text
