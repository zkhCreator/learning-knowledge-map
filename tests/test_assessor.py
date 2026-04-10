"""
tests/test_assessor.py

Unit tests for src/agents/assessor.py (Initial Assessment).
All LLM calls and input() are mocked — no real API calls.

TDD: tests written before implementation.
"""

import pytest
from unittest.mock import patch, MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_chain(make_node, make_edge, n=3):
    """
    Create a linear chain of n nodes:
        node[0] (depth=1) → node[1] (depth=2) → node[2] (depth=3)
    Returns list of nodes ordered shallowest to deepest.
    """
    from src.db import database as db
    goal = db.create_goal("Chain Goal")
    nodes = [
        make_node(title=f"Node{i}", goal_id=goal["id"], depth_level=i + 1)
        for i in range(n)
    ]
    # node[i] is prerequisite for node[i+1]
    for i in range(n - 1):
        make_edge(from_node=nodes[i]["id"], to_node=nodes[i + 1]["id"])
    return nodes, goal


def _make_tree(make_node, make_edge):
    """
    Create a two-branch tree:
        root(depth=1) → mid_a(depth=2) → leaf_a(depth=3)
                      → mid_b(depth=2) → leaf_b(depth=3)
    Returns (root, mid_a, mid_b, leaf_a, leaf_b), goal
    """
    from src.db import database as db
    goal = db.create_goal("Tree Goal")
    root  = make_node(title="Root",   goal_id=goal["id"], depth_level=1)
    mid_a = make_node(title="Mid A",  goal_id=goal["id"], depth_level=2)
    mid_b = make_node(title="Mid B",  goal_id=goal["id"], depth_level=2)
    leaf_a = make_node(title="Leaf A", goal_id=goal["id"], depth_level=3)
    leaf_b = make_node(title="Leaf B", goal_id=goal["id"], depth_level=3)
    make_edge(from_node=root["id"],  to_node=mid_a["id"])
    make_edge(from_node=root["id"],  to_node=mid_b["id"])
    make_edge(from_node=mid_a["id"], to_node=leaf_a["id"])
    make_edge(from_node=mid_b["id"], to_node=leaf_b["id"])
    return (root, mid_a, mid_b, leaf_a, leaf_b), goal


# ── conftest edge fixture ──────────────────────────────────────────────────────
# We need a make_edge fixture — define it here as a local fixture

@pytest.fixture
def make_edge(tmp_db):
    from src.db import database as db
    def _factory(from_node: str, to_node: str, edge_type: str = "prerequisite"):
        return db.create_edge(from_node=from_node, to_node=to_node, edge_type=edge_type)
    return _factory


# ── next_probe_node ────────────────────────────────────────────────────────────

class TestNextProbeNode:
    def test_empty_goal_returns_none(self, tmp_db):
        from src.db import database as db
        goal = db.create_goal("Empty Goal")
        from src.agents.assessor import next_probe_node
        result = next_probe_node(goal["id"], history=[], self_report=3)
        assert result is None

    def test_self_report_1_returns_shallowest_node(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.agents.assessor import next_probe_node
        result = next_probe_node(goal["id"], history=[], self_report=1)
        assert result is not None
        assert result["depth_level"] == min(n["depth_level"] for n in nodes)

    def test_self_report_5_returns_deepest_node(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.agents.assessor import next_probe_node
        result = next_probe_node(goal["id"], history=[], self_report=5)
        assert result is not None
        assert result["depth_level"] == max(n["depth_level"] for n in nodes)

    def test_self_report_3_returns_middle_node(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=5)
        from src.agents.assessor import next_probe_node
        result = next_probe_node(goal["id"], history=[], self_report=3)
        assert result is not None
        depths = sorted({n["depth_level"] for n in nodes})
        middle_depth = depths[len(depths) // 2]
        assert result["depth_level"] == middle_depth

    def test_pass_probes_deeper_next(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.agents.assessor import next_probe_node
        history = [{"node_id": nodes[0]["id"], "depth_level": 1, "score": 0.9, "passed": True}]
        result = next_probe_node(goal["id"], history=history, self_report=3)
        assert result is not None
        assert result["depth_level"] > 1

    def test_fail_probes_shallower_next(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.agents.assessor import next_probe_node
        # Start from deepest, then fail
        history = [{"node_id": nodes[2]["id"], "depth_level": 3, "score": 0.2, "passed": False}]
        result = next_probe_node(goal["id"], history=history, self_report=5)
        assert result is not None
        assert result["depth_level"] < 3

    def test_three_consecutive_fails_returns_none(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=5)
        from src.agents.assessor import next_probe_node
        history = [
            {"node_id": nodes[i]["id"], "depth_level": i + 1, "score": 0.1, "passed": False}
            for i in range(3)
        ]
        result = next_probe_node(goal["id"], history=history, self_report=3)
        assert result is None

    def test_three_consecutive_passes_returns_none(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=5)
        from src.agents.assessor import next_probe_node
        history = [
            {"node_id": nodes[i]["id"], "depth_level": i + 1, "score": 0.95, "passed": True}
            for i in range(3)
        ]
        result = next_probe_node(goal["id"], history=history, self_report=3)
        assert result is None

    def test_already_probed_nodes_skipped(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=2)
        from src.agents.assessor import next_probe_node
        # Probe first node, pass — next should NOT return same node
        history = [{"node_id": nodes[0]["id"], "depth_level": 1, "score": 0.9, "passed": True}]
        result = next_probe_node(goal["id"], history=history, self_report=1)
        assert result is None or result["id"] != nodes[0]["id"]

    def test_all_nodes_probed_returns_none(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=2)
        from src.agents.assessor import next_probe_node
        history = [
            {"node_id": n["id"], "depth_level": n["depth_level"], "score": 0.5, "passed": False}
            for n in nodes
        ]
        result = next_probe_node(goal["id"], history=history, self_report=3)
        assert result is None

    def test_prefers_standard_over_familiarity(self, tmp_db, make_node, make_edge):
        """When two nodes are at the same depth, prefer non-familiarity ones."""
        from src.db import database as db
        goal = db.create_goal("Pref Goal")
        fam  = make_node(title="Familiarity Node", goal_id=goal["id"],
                         depth_level=2, strictness_level="familiarity")
        std  = make_node(title="Standard Node",    goal_id=goal["id"],
                         depth_level=2, strictness_level="standard")
        from src.agents.assessor import next_probe_node
        result = next_probe_node(goal["id"], history=[], self_report=3)
        assert result is not None
        assert result["strictness_level"] != "familiarity"

    def test_max_probes_cap(self, tmp_db, make_node, make_edge):
        """Should return None once MAX_PROBES history items are reached."""
        from src.db import database as db
        from src import config
        goal = db.create_goal("Big Goal")
        nodes = [
            make_node(title=f"N{i}", goal_id=goal["id"], depth_level=i + 1)
            for i in range(20)
        ]
        from src.agents.assessor import next_probe_node, MAX_PROBES
        history = [
            {"node_id": nodes[i]["id"], "depth_level": i + 1, "score": 0.7, "passed": True}
            for i in range(MAX_PROBES)
        ]
        result = next_probe_node(goal["id"], history=history, self_report=3)
        assert result is None


# ── _propagate_mastery ─────────────────────────────────────────────────────────

class TestPropagatemastery:
    def test_single_node_marked_mastered(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.assessor import _propagate_mastery
        node = make_node()
        _propagate_mastery(node["id"], user_id="default", inferred_score=0.85)
        state = db.get_state(node["id"], "default")
        assert state["status"] == "mastered"
        assert state["raw_score"] == pytest.approx(0.85)

    def test_prerequisites_marked_mastered_transitively(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.db import database as db
        from src.agents.assessor import _propagate_mastery
        # Pass the deepest node — all prerequisites should also be mastered
        _propagate_mastery(nodes[2]["id"], user_id="default", inferred_score=0.85)
        for node in nodes:
            state = db.get_state(node["id"], "default")
            assert state["status"] == "mastered", f"Node {node['title']} should be mastered"

    def test_does_not_mark_dependents(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.db import database as db
        from src.agents.assessor import _propagate_mastery
        # Pass the middle node — deepest should NOT be marked
        _propagate_mastery(nodes[1]["id"], user_id="default", inferred_score=0.85)
        deep_state = db.get_state(nodes[2]["id"], "default")
        # Deepest node should not have been touched
        assert not deep_state or deep_state.get("status") != "mastered"

    def test_no_prereqs_just_marks_node(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.assessor import _propagate_mastery
        node = make_node()
        _propagate_mastery(node["id"], user_id="default", inferred_score=0.82)
        state = db.get_state(node["id"], "default")
        assert state["status"] == "mastered"

    def test_diamond_prereqs_no_double_mark(self, tmp_db, make_node, make_edge):
        """Diamond: A→B, A→C, B→D, C→D — passing D should mark all."""
        from src.db import database as db
        goal = db.create_goal("Diamond")
        a = make_node(title="A", goal_id=goal["id"], depth_level=1)
        b = make_node(title="B", goal_id=goal["id"], depth_level=2)
        c = make_node(title="C", goal_id=goal["id"], depth_level=2)
        d = make_node(title="D", goal_id=goal["id"], depth_level=3)
        make_edge(a["id"], b["id"])
        make_edge(a["id"], c["id"])
        make_edge(b["id"], d["id"])
        make_edge(c["id"], d["id"])
        from src.agents.assessor import _propagate_mastery
        _propagate_mastery(d["id"], user_id="default", inferred_score=0.85)
        for node in [a, b, c, d]:
            state = db.get_state(node["id"], "default")
            assert state["status"] == "mastered"


# ── _propagate_unknown ─────────────────────────────────────────────────────────

class TestPropagateUnknown:
    def test_single_node_marked_unknown(self, tmp_db, make_node):
        from src.db import database as db
        from src.agents.assessor import _propagate_unknown
        node = make_node()
        _propagate_unknown(node["id"], user_id="default")
        state = db.get_state(node["id"], "default")
        assert state["status"] == "unknown"

    def test_dependents_marked_unknown_transitively(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.db import database as db
        from src.agents.assessor import _propagate_unknown
        # Fail the shallowest node — all dependents should also be unknown
        _propagate_unknown(nodes[0]["id"], user_id="default")
        for node in nodes:
            state = db.get_state(node["id"], "default")
            assert state["status"] == "unknown", f"Node {node['title']} should be unknown"

    def test_does_not_mark_prerequisites(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.db import database as db
        from src.agents.assessor import _propagate_mastery, _propagate_unknown
        # First mark all as mastered
        for n in nodes:
            _propagate_mastery(n["id"], user_id="default", inferred_score=0.85)
        # Now fail the middle node — shallowest prereq should NOT be touched
        _propagate_unknown(nodes[1]["id"], user_id="default")
        root_state = db.get_state(nodes[0]["id"], "default")
        assert root_state["status"] == "mastered"

    def test_branch_propagation(self, tmp_db, make_node, make_edge):
        """Failing mid_a should mark leaf_a unknown but NOT mid_b/leaf_b."""
        (root, mid_a, mid_b, leaf_a, leaf_b), goal = _make_tree(make_node, make_edge)
        from src.db import database as db
        from src.agents.assessor import _propagate_unknown
        _propagate_unknown(mid_a["id"], user_id="default")
        assert db.get_state(leaf_a["id"], "default")["status"] == "unknown"
        # mid_b and leaf_b should be untouched (no state row, or not unknown)
        mid_b_state = db.get_state(mid_b["id"], "default")
        assert not mid_b_state or mid_b_state.get("status") != "unknown"


# ── generate_probe_question ────────────────────────────────────────────────────

class TestGenerateProbeQuestion:
    @patch("src.agents.assessor.llm.call_json")
    def test_returns_question_and_answer(self, mock_llm, tmp_db, make_node):
        mock_llm.return_value = {
            "question": "What is X?",
            "expected_answer": "X is the concept of ...",
        }
        from src.agents.assessor import generate_probe_question
        node = make_node(title="X Concept")
        result = generate_probe_question(node)
        assert "question" in result
        assert "expected_answer" in result
        assert result["question"] == "What is X?"

    @patch("src.agents.assessor.llm.call_json")
    def test_api_failure_raises(self, mock_llm, tmp_db, make_node):
        mock_llm.side_effect = RuntimeError("API timeout")
        from src.agents.assessor import generate_probe_question
        node = make_node()
        with pytest.raises(RuntimeError):
            generate_probe_question(node)

    @patch("src.agents.assessor.llm.call_json")
    def test_non_dict_response_raises(self, mock_llm, tmp_db, make_node):
        mock_llm.return_value = "just a string"
        from src.agents.assessor import generate_probe_question
        node = make_node()
        with pytest.raises(ValueError):
            generate_probe_question(node)


# ── run_assessment_loop ────────────────────────────────────────────────────────

class TestRunAssessmentLoop:
    """
    Heavy mocking: input(), score_answer(), generate_probe_question().
    """

    def _mock_inputs(self, monkeypatch, answers: list[str]):
        """Feed a list of answers into input() calls."""
        answers_iter = iter(answers)
        monkeypatch.setattr("builtins.input", lambda _: next(answers_iter, "/exit"))

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_expert_user_all_pass(self, mock_gen, mock_score, tmp_db, make_node, make_edge,
                                  monkeypatch):
        """Self-report 5, all questions pass → most nodes marked mastered."""
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        mock_gen.return_value = {"question": "Q?", "expected_answer": "A."}
        mock_score.return_value = {"score": 0.95, "error_type": None,
                                   "explanation": "Correct.", "related_concepts": []}
        # self-report answer + 3 question answers
        self._mock_inputs(monkeypatch, ["5", "answer", "answer", "answer"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        assert result["mastered"] > 0

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_beginner_all_fail_early_exit(self, mock_gen, mock_score, tmp_db, make_node, make_edge,
                                          monkeypatch):
        """Self-report 1, all questions fail → early exit after 3 fails."""
        nodes, goal = _make_chain(make_node, make_edge, n=5)
        mock_gen.return_value = {"question": "Q?", "expected_answer": "A."}
        mock_score.return_value = {"score": 0.1, "error_type": "fundamental_misunderstanding",
                                   "explanation": "Wrong.", "related_concepts": []}
        self._mock_inputs(monkeypatch, ["1", "wrong", "wrong", "wrong", "wrong", "wrong"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        # Should have stopped early — few probes despite 5 nodes
        assert result["probes_done"] <= 3
        assert result["mastered"] == 0

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_user_exits_early_preserves_results(self, mock_gen, mock_score,
                                                 tmp_db, make_node, make_edge, monkeypatch):
        """User types /exit — partial results should still be saved."""
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        mock_gen.return_value = {"question": "Q?", "expected_answer": "A."}
        mock_score.return_value = {"score": 0.9, "error_type": None,
                                   "explanation": "Good.", "related_concepts": []}
        # Answer first question, then exit
        self._mock_inputs(monkeypatch, ["3", "good answer", "/exit"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        assert result["probes_done"] >= 1

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_empty_goal_returns_gracefully(self, mock_gen, mock_score,
                                           tmp_db, monkeypatch):
        """No nodes → should return immediately without crashing."""
        from src.db import database as db
        goal = db.create_goal("Empty")
        self._mock_inputs(monkeypatch, ["3"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        assert result["probes_done"] == 0

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_mixed_scores_finds_boundary(self, mock_gen, mock_score,
                                         tmp_db, make_node, make_edge, monkeypatch):
        """Pass shallow nodes, fail deep ones → boundary correctly identified."""
        nodes, goal = _make_chain(make_node, make_edge, n=4)
        mock_gen.return_value = {"question": "Q?", "expected_answer": "A."}

        call_count = [0]
        def score_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First calls pass, later fail
            score = 0.9 if call_count[0] <= 2 else 0.2
            return {"score": score, "error_type": None if score > 0.5 else "incomplete",
                    "explanation": ".", "related_concepts": []}

        mock_score.side_effect = score_side_effect
        self._mock_inputs(monkeypatch, ["3", "ans", "ans", "ans", "ans", "ans"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        assert result["probes_done"] >= 2
        # Some mastered, some not
        assert result["mastered"] >= 1

    @patch("src.agents.examiner.score_answer")
    @patch("src.agents.assessor.generate_probe_question")
    def test_result_contains_expected_keys(self, mock_gen, mock_score,
                                           tmp_db, make_node, make_edge, monkeypatch):
        """Return dict always has: mastered, unknown, probes_done, total_nodes."""
        nodes, goal = _make_chain(make_node, make_edge, n=2)
        mock_gen.return_value = {"question": "Q?", "expected_answer": "A."}
        mock_score.return_value = {"score": 0.9, "error_type": None,
                                   "explanation": "Good.", "related_concepts": []}
        self._mock_inputs(monkeypatch, ["4", "answer", "answer"])

        from src.agents.assessor import run_assessment_loop
        from rich.console import Console
        result = run_assessment_loop(goal["id"], user_id="default",
                                     model=None, console=Console(quiet=True))
        for key in ("mastered", "unknown", "probes_done", "total_nodes"):
            assert key in result, f"Missing key: {key}"
