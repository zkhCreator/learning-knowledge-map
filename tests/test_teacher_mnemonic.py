"""
tests/test_teacher_mnemonic.py

Unit tests for mnemonic integration in the teacher agent's outline generation.
Verifies that when a user has a cognitive profile, the outline prompt is augmented
with mnemonic strategy instructions, and the resulting sections include mnemonic fields.

All LLM calls are mocked.
"""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestOutlineWithMnemonic:
    """Test that generate_outline integrates mnemonic strategy into the prompt."""

    def _mock_sections_with_mnemonic(self, strategy="spatial"):
        """Return mock outline sections that include mnemonic fields."""
        return {
            "sections": [
                {
                    "index": 1,
                    "title": "WAL 日志基本原理",
                    "content": "Write-Ahead Logging 的核心思想是...",
                    "needs_search": False,
                    "sources": [],
                    "analogy": "类似于写代码前先记录修改计划",
                    "analogy_source_node": "版本控制",
                    "covered": False,
                    "mnemonic": {
                        "strategy": strategy,
                        "content": "想象银行大楼入口，保安要求先签登记簿...",
                        "palace_location": "一楼大厅入口" if strategy == "spatial" else None,
                    },
                },
                {
                    "index": 2,
                    "title": "Checkpoint 机制",
                    "content": "Checkpoint 定期将内存中的脏页刷写到磁盘...",
                    "needs_search": False,
                    "sources": [],
                    "analogy": None,
                    "analogy_source_node": None,
                    "covered": False,
                    "mnemonic": {
                        "strategy": strategy,
                        "content": "银行每天下班前要盘点一次，把所有临时记录整理归档...",
                        "palace_location": "一楼保险库" if strategy == "spatial" else None,
                    },
                },
            ]
        }

    @patch("src.agents.teacher.llm.call_json")
    def test_outline_includes_mnemonic_when_profile_exists(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import generate_outline

        node = make_node(title="WAL 日志与事务恢复")

        # Create a cognitive profile for the user
        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )

        # Mock: forward agent returns sections with mnemonic, reverse agent approves
        mock_call_json.side_effect = [
            self._mock_sections_with_mnemonic("spatial"),
            {"approved": True, "issues": [], "corrections": {}},
        ]

        outline = generate_outline(node_id=node["id"], force_regenerate=True)

        assert outline is not None
        sections = outline["sections"]
        assert len(sections) == 2

        # Check mnemonic field exists in sections
        for sec in sections:
            assert "mnemonic" in sec
            assert sec["mnemonic"]["strategy"] == "spatial"
            assert sec["mnemonic"]["content"]

    @patch("src.agents.teacher.llm.call_json")
    def test_outline_prompt_includes_mnemonic_instructions(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import generate_outline

        node = make_node(title="数据库事务")

        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.2,
            symbolic_weight=0.6,
            narrative_weight=0.2,
            assessed=True,
        )

        mock_call_json.side_effect = [
            self._mock_sections_with_mnemonic("symbolic"),
            {"approved": True, "issues": [], "corrections": {}},
        ]

        generate_outline(node_id=node["id"], force_regenerate=True)

        # Verify the forward agent was called with mnemonic instructions in system or user prompt
        call_args = mock_call_json.call_args_list[0]
        # call_args[0] = positional args: (system_prompt, user_prompt, ...)
        system_prompt = call_args[0][0] if call_args[0] else call_args[1].get("system", "")
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("user", "")
        combined = system_prompt + user_prompt
        # Should mention mnemonic or the strategy type
        assert "mnemonic" in combined or "助记" in combined or "逻辑" in combined or "规则链" in combined

    @patch("src.agents.teacher.llm.call_json")
    def test_outline_without_profile_has_no_mnemonic(self, mock_call_json, make_node):
        from src.agents.teacher import generate_outline

        node = make_node(title="基础概念")

        # No cognitive profile created
        plain_sections = {
            "sections": [
                {
                    "index": 1,
                    "title": "基础概念一",
                    "content": "内容...",
                    "needs_search": False,
                    "sources": [],
                    "analogy": None,
                    "analogy_source_node": None,
                    "covered": False,
                }
            ]
        }

        mock_call_json.side_effect = [
            plain_sections,
            {"approved": True, "issues": [], "corrections": {}},
        ]

        outline = generate_outline(node_id=node["id"], force_regenerate=True)
        sections = outline["sections"]
        # Without a profile, mnemonic may be absent or None
        for sec in sections:
            mnemonic = sec.get("mnemonic")
            assert mnemonic is None or mnemonic == {}

    @patch("src.agents.teacher.llm.call_json")
    def test_mnemonic_anchors_persisted_to_db(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import generate_outline

        node = make_node(title="WAL 日志")

        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )

        mock_call_json.side_effect = [
            self._mock_sections_with_mnemonic("spatial"),
            {"approved": True, "issues": [], "corrections": {}},
        ]

        generate_outline(node_id=node["id"], force_regenerate=True)

        # Verify anchors were saved to DB
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert len(anchors) == 2
        assert anchors[0]["strategy"] == "spatial"


class TestSocraticWithMnemonic:
    """Test that Socratic dialogue incorporates mnemonic hints."""

    @patch("src.agents.teacher.llm.call_json")
    def test_chat_turn_prompt_includes_mnemonic_context(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import chat_turn

        node = make_node(title="WAL 日志")

        db.create_cognitive_profile(
            user_id="default",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )

        outline = db.create_outline(
            node_id=node["id"],
            sections=[
                {"index": 1, "title": "Section 1", "content": "Content", "sources": [],
                 "analogy": None, "analogy_source_node": None, "covered": False,
                 "mnemonic": {"strategy": "spatial", "content": "银行大楼入口", "palace_location": "一楼大厅"}},
            ],
        )
        db.update_outline(outline["id"], status="active")
        session = db.create_learning_session(node_id=node["id"], outline_id=outline["id"])

        mock_call_json.return_value = {
            "response": "很好，想象你走进银行大楼...",
            "newly_covered_sections": [1],
        }

        response_text, progress, covered = chat_turn(
            session=session,
            node=node,
            outline_sections=[
                {"index": 1, "title": "Section 1", "content": "Content", "sources": [],
                 "analogy": None, "analogy_source_node": None, "covered": False,
                 "mnemonic": {"strategy": "spatial", "content": "银行大楼入口", "palace_location": "一楼大厅"}},
            ],
            user_message="什么是 WAL？",
            history=[],
        )

        assert response_text
        # The Socratic prompt should have included mnemonic info
        call_args = mock_call_json.call_args
        system_prompt = call_args[0][0] if call_args[0] else ""
        user_prompt = call_args[0][1] if len(call_args[0]) > 1 else ""
        combined = system_prompt + user_prompt
        # Should reference the mnemonic or spatial context somewhere
        assert "助记" in combined or "mnemonic" in combined or "银行" in combined or "空间" in combined
