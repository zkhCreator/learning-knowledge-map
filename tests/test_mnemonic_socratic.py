"""
tests/test_mnemonic_socratic.py

Unit tests for enhanced mnemonic integration in Socratic dialogue.
Tests that:
    - _format_outline_for_prompt includes mnemonic info when present
    - Socratic system prompt includes mnemonic guidance rule
    - chat_turn correctly passes mnemonic context to the LLM

All LLM calls are mocked.
"""

import json
import pytest
from unittest.mock import patch


class TestOutlineFormatWithMnemonic:
    """Verify _format_outline_for_prompt includes mnemonic data."""

    def test_format_includes_spatial_mnemonic(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = [
            {
                "index": 1,
                "title": "WAL 基本原理",
                "content": "Write-Ahead Logging 的核心...",
                "analogy": "类似于先写日志再操作",
                "covered": False,
                "mnemonic": {
                    "strategy": "spatial",
                    "content": "银行大楼入口，保安要求签名",
                    "palace_location": "一楼大厅",
                },
            }
        ]
        result = _format_outline_for_prompt(sections)
        assert "助记" in result
        assert "银行大楼入口" in result
        assert "spatial" in result

    def test_format_includes_symbolic_mnemonic(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = [
            {
                "index": 1,
                "title": "事务隔离级别",
                "content": "...",
                "analogy": None,
                "covered": False,
                "mnemonic": {
                    "strategy": "symbolic",
                    "content": "三级隔离: 读未提交 < 读已提交 < 可重复读",
                    "palace_location": None,
                },
            }
        ]
        result = _format_outline_for_prompt(sections)
        assert "助记" in result
        assert "三级隔离" in result

    def test_format_no_mnemonic_field(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = [
            {
                "index": 1,
                "title": "基础概念",
                "content": "内容...",
                "analogy": None,
                "covered": False,
            }
        ]
        result = _format_outline_for_prompt(sections)
        assert "助记" not in result

    def test_format_mnemonic_none(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = [
            {
                "index": 1,
                "title": "基础概念",
                "content": "内容...",
                "analogy": None,
                "covered": False,
                "mnemonic": None,
            }
        ]
        result = _format_outline_for_prompt(sections)
        assert "助记" not in result

    def test_format_mnemonic_empty_dict(self):
        from src.agents.teacher import _format_outline_for_prompt
        sections = [
            {
                "index": 1,
                "title": "基础概念",
                "content": "内容...",
                "analogy": None,
                "covered": False,
                "mnemonic": {},
            }
        ]
        result = _format_outline_for_prompt(sections)
        assert "助记" not in result


class TestSocraticSystemPrompt:
    """Verify the Socratic system prompt includes mnemonic guidance."""

    def test_socratic_prompt_has_mnemonic_rule(self):
        from src.agents.teacher import SOCRATIC_SYSTEM
        # The prompt template should mention mnemonic/助记
        assert "助记" in SOCRATIC_SYSTEM or "mnemonic" in SOCRATIC_SYSTEM


class TestChatTurnWithMnemonic:
    """Verify chat_turn sends mnemonic-enriched context to the LLM."""

    @patch("src.agents.teacher.llm.call_json")
    def test_chat_turn_with_mnemonic_sections(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import chat_turn

        node = make_node(title="WAL 日志")
        sections = [
            {
                "index": 1,
                "title": "WAL 基本原理",
                "content": "核心思想是先写日志...",
                "sources": [],
                "analogy": None,
                "analogy_source_node": None,
                "covered": False,
                "mnemonic": {
                    "strategy": "spatial",
                    "content": "银行大楼入口的保安登记簿",
                    "palace_location": "一楼大厅入口",
                },
            },
        ]

        # Create outline and session
        outline = db.create_outline(node_id=node["id"], sections=sections)
        db.update_outline(outline["id"], status="active")
        session = db.create_learning_session(
            node_id=node["id"], outline_id=outline["id"]
        )

        mock_call_json.return_value = {
            "response": "让我们从一个场景开始想象...",
            "newly_covered_sections": [1],
        }

        response_text, progress, covered = chat_turn(
            session=session,
            node=node,
            outline_sections=sections,
            user_message="什么是 WAL？",
            history=[],
        )

        assert response_text
        assert 1 in covered

        # Verify the system prompt sent to LLM includes mnemonic context
        call_args = mock_call_json.call_args
        system_prompt = call_args[0][0]
        # The formatted outline in the system prompt should include the mnemonic
        assert "银行大楼入口" in system_prompt or "助记" in system_prompt

    @patch("src.agents.teacher.llm.call_json")
    def test_chat_turn_without_mnemonic(self, mock_call_json, make_node):
        from src.db import database as db
        from src.agents.teacher import chat_turn

        node = make_node(title="基础概念")
        sections = [
            {
                "index": 1,
                "title": "基础",
                "content": "内容...",
                "sources": [],
                "analogy": None,
                "analogy_source_node": None,
                "covered": False,
            },
        ]

        outline = db.create_outline(node_id=node["id"], sections=sections)
        db.update_outline(outline["id"], status="active")
        session = db.create_learning_session(
            node_id=node["id"], outline_id=outline["id"]
        )

        mock_call_json.return_value = {
            "response": "让我们来看看...",
            "newly_covered_sections": [1],
        }

        response_text, progress, covered = chat_turn(
            session=session,
            node=node,
            outline_sections=sections,
            user_message="开始学习",
            history=[],
        )

        assert response_text
        # System prompt should NOT contain mnemonic reference
        call_args = mock_call_json.call_args
        system_prompt = call_args[0][0]
        assert "🧠 助记" not in system_prompt
