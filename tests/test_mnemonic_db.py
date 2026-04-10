"""
tests/test_mnemonic_db.py

Unit tests for mnemonic strategy layer database operations:
    - user_cognitive_profile CRUD
    - mnemonic_anchors CRUD
    - palace_layouts CRUD

No mocks needed — uses the tmp_db fixture (temp SQLite).
"""

import json
import pytest


# ── Cognitive Profile ─────────────────────────────────────────────────────────

class TestCognitiveProfile:
    def test_create_profile_defaults(self, tmp_db):
        from src.db import database as db
        profile = db.create_cognitive_profile(user_id="alice")
        assert profile["user_id"] == "alice"
        assert abs(profile["spatial_weight"] - 0.33) < 0.01
        assert abs(profile["symbolic_weight"] - 0.33) < 0.01
        assert abs(profile["narrative_weight"] - 0.34) < 0.01
        assert profile["assessed"] is False

    def test_create_profile_custom_weights(self, tmp_db):
        from src.db import database as db
        profile = db.create_cognitive_profile(
            user_id="bob",
            spatial_weight=0.6,
            symbolic_weight=0.3,
            narrative_weight=0.1,
            assessed=True,
        )
        assert abs(profile["spatial_weight"] - 0.6) < 0.01
        assert profile["assessed"] is True

    def test_get_profile_existing(self, tmp_db):
        from src.db import database as db
        db.create_cognitive_profile(user_id="alice")
        fetched = db.get_cognitive_profile("alice")
        assert fetched is not None
        assert fetched["user_id"] == "alice"

    def test_get_profile_missing_returns_none(self, tmp_db):
        from src.db import database as db
        result = db.get_cognitive_profile("nonexistent")
        assert result is None

    def test_update_profile_weights(self, tmp_db):
        from src.db import database as db
        db.create_cognitive_profile(user_id="alice")
        db.update_cognitive_profile(
            "alice",
            spatial_weight=0.7,
            symbolic_weight=0.2,
            narrative_weight=0.1,
            assessed=True,
        )
        fetched = db.get_cognitive_profile("alice")
        assert abs(fetched["spatial_weight"] - 0.7) < 0.01
        assert fetched["assessed"] is True

    def test_get_dominant_strategy(self, tmp_db):
        from src.db import database as db
        db.create_cognitive_profile(
            user_id="alice",
            spatial_weight=0.6,
            symbolic_weight=0.3,
            narrative_weight=0.1,
        )
        profile = db.get_cognitive_profile("alice")
        # The dominant strategy should be spatial
        strategies = {
            "spatial": profile["spatial_weight"],
            "symbolic": profile["symbolic_weight"],
            "narrative": profile["narrative_weight"],
        }
        dominant = max(strategies, key=strategies.get)
        assert dominant == "spatial"

    def test_create_duplicate_profile_raises(self, tmp_db):
        from src.db import database as db
        db.create_cognitive_profile(user_id="alice")
        with pytest.raises(Exception):
            db.create_cognitive_profile(user_id="alice")

    def test_weights_boundary_zero(self, tmp_db):
        from src.db import database as db
        profile = db.create_cognitive_profile(
            user_id="zero",
            spatial_weight=0.0,
            symbolic_weight=0.0,
            narrative_weight=1.0,
        )
        assert profile["spatial_weight"] == 0.0
        assert profile["narrative_weight"] == 1.0


# ── Mnemonic Anchors ──────────────────────────────────────────────────────────

class TestMnemonicAnchors:
    def test_create_anchor(self, make_node):
        from src.db import database as db
        node = make_node()
        anchor = db.create_mnemonic_anchor(
            user_id="default",
            node_id=node["id"],
            strategy="spatial",
            section_index=1,
            content="想象银行大楼入口，保安要求先签登记簿...",
            palace_location="一楼大厅入口",
        )
        assert anchor["strategy"] == "spatial"
        assert anchor["section_index"] == 1
        assert anchor["palace_location"] == "一楼大厅入口"
        assert anchor["effectiveness"] is None

    def test_create_anchor_symbolic(self, make_node):
        from src.db import database as db
        node = make_node()
        anchor = db.create_mnemonic_anchor(
            user_id="default",
            node_id=node["id"],
            strategy="symbolic",
            section_index=1,
            content="规则链: 先写日志 → 再执行操作 → 崩溃后重放日志",
        )
        assert anchor["strategy"] == "symbolic"
        assert anchor["palace_location"] is None

    def test_create_anchor_narrative(self, make_node):
        from src.db import database as db
        node = make_node()
        anchor = db.create_mnemonic_anchor(
            user_id="default",
            node_id=node["id"],
            strategy="narrative",
            section_index=1,
            content="银行柜员的一天：每笔交易前都要先在日志本上记录...",
        )
        assert anchor["strategy"] == "narrative"

    def test_create_anchor_goal_level_null_section(self, make_node):
        from src.db import database as db
        node = make_node()
        anchor = db.create_mnemonic_anchor(
            user_id="default",
            node_id=node["id"],
            strategy="spatial",
            section_index=None,
            content="整栋银行大楼：一楼基础概念，二楼核心机制，三楼高级应用",
            palace_location="大楼整体",
        )
        assert anchor["section_index"] is None

    def test_get_anchors_for_node(self, make_node):
        from src.db import database as db
        node = make_node()
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1, content="锚点1",
        )
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=2, content="锚点2",
        )
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert len(anchors) == 2

    def test_get_anchors_empty(self, make_node):
        from src.db import database as db
        node = make_node()
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert anchors == []

    def test_update_anchor_effectiveness(self, make_node):
        from src.db import database as db
        node = make_node()
        anchor = db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1, content="测试锚点",
        )
        db.update_mnemonic_anchor(anchor["id"], effectiveness=0.85)
        anchors = db.get_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert abs(anchors[0]["effectiveness"] - 0.85) < 0.01

    def test_duplicate_anchor_same_section_raises(self, make_node):
        from src.db import database as db
        node = make_node()
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1, content="第一个",
        )
        with pytest.raises(Exception):
            db.create_mnemonic_anchor(
                user_id="default", node_id=node["id"],
                strategy="spatial", section_index=1, content="第二个",
            )

    def test_delete_anchors_for_node(self, make_node):
        from src.db import database as db
        node = make_node()
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=1, content="锚点1",
        )
        db.create_mnemonic_anchor(
            user_id="default", node_id=node["id"],
            strategy="spatial", section_index=2, content="锚点2",
        )
        deleted = db.delete_mnemonic_anchors(node_id=node["id"], user_id="default")
        assert deleted == 2
        assert db.get_mnemonic_anchors(node_id=node["id"], user_id="default") == []


# ── Palace Layouts ────────────────────────────────────────────────────────────

class TestPalaceLayouts:
    def test_create_palace_layout(self, make_goal):
        from src.db import database as db
        goal = make_goal()
        layout = db.create_palace_layout(
            user_id="default",
            goal_id=goal["id"],
            layout_desc="一栋三层银行大楼，一楼是基础概念，二楼是核心机制，三楼是高级应用",
            location_map={"node_1": "一楼大厅", "node_2": "一楼柜台"},
        )
        assert layout["goal_id"] == goal["id"]
        assert "三层银行大楼" in layout["layout_desc"]
        assert layout["location_map"]["node_1"] == "一楼大厅"

    def test_get_palace_layout(self, make_goal):
        from src.db import database as db
        goal = make_goal()
        db.create_palace_layout(
            user_id="default", goal_id=goal["id"],
            layout_desc="测试宫殿", location_map={},
        )
        fetched = db.get_palace_layout(goal_id=goal["id"], user_id="default")
        assert fetched is not None
        assert fetched["layout_desc"] == "测试宫殿"

    def test_get_palace_layout_missing(self, make_goal):
        from src.db import database as db
        result = db.get_palace_layout(goal_id="nonexistent", user_id="default")
        assert result is None

    def test_update_palace_layout(self, make_goal):
        from src.db import database as db
        goal = make_goal()
        layout = db.create_palace_layout(
            user_id="default", goal_id=goal["id"],
            layout_desc="初始布局", location_map={"a": "入口"},
        )
        db.update_palace_layout(
            layout["id"],
            layout_desc="更新后的布局",
            location_map={"a": "入口", "b": "大厅"},
        )
        fetched = db.get_palace_layout(goal_id=goal["id"], user_id="default")
        assert fetched["layout_desc"] == "更新后的布局"
        assert "b" in fetched["location_map"]

    def test_duplicate_palace_for_same_goal_raises(self, make_goal):
        from src.db import database as db
        goal = make_goal()
        db.create_palace_layout(
            user_id="default", goal_id=goal["id"],
            layout_desc="第一个", location_map={},
        )
        with pytest.raises(Exception):
            db.create_palace_layout(
                user_id="default", goal_id=goal["id"],
                layout_desc="第二个", location_map={},
            )
