"""
tests/test_assessment_service.py

Unit tests for src/services/assessment.py (stateless goal assessment service).

The service is a non-interactive decomposition of agents.assessor.run_assessment_loop:
each call is a fresh process, so in-flight state (history + probe context) is
carried through the request/response cycle by the client. Only durable results
are persisted to user_knowledge_state via db.upsert_state.

All LLM-backed calls are mocked — no real API calls. We patch the names as the
service imports them: src.services.assessment.generate_probe_question and
src.services.assessment.score_answer.

TDD: tests written before implementation.
"""

import pytest
from unittest.mock import patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_chain(make_node, make_edge, n=3):
    """Linear chain: node[0](depth=1) -> node[1](depth=2) -> node[2](depth=3)."""
    from src.data import database as db
    goal = db.create_goal("Chain Goal")
    nodes = [
        make_node(title=f"Node{i}", goal_id=goal["id"], depth_level=i + 1)
        for i in range(n)
    ]
    for i in range(n - 1):
        make_edge(from_node=nodes[i]["id"], to_node=nodes[i + 1]["id"])
    return nodes, goal


@pytest.fixture
def make_edge(tmp_db):
    from src.data import database as db

    def _factory(from_node: str, to_node: str, edge_type: str = "prerequisite"):
        return db.create_edge(from_node=from_node, to_node=to_node, edge_type=edge_type)

    return _factory


def _probe_qa(question="Q?", expected_answer="A."):
    return {"question": question, "expected_answer": expected_answer}


def _score(score, error_type=None):
    return {
        "score": score,
        "error_type": error_type,
        "explanation": "",
        "related_concepts": [],
    }


# ── start_assessment ─────────────────────────────────────────────────────────────

class TestStartAssessment:
    def test_unknown_goal_raises_goal_not_found(self, tmp_db):
        from src.services.assessment import start_assessment, AssessmentError
        with pytest.raises(AssessmentError) as exc:
            start_assessment(goal="nope", user_id="default", self_report=3)
        assert exc.value.code == "goal_not_found"

    def test_invalid_self_report_raises(self, tmp_db, make_node, make_edge):
        _nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import start_assessment, AssessmentError
        with pytest.raises(AssessmentError) as exc:
            start_assessment(goal=goal["id"], user_id="default", self_report=9)
        assert exc.value.code == "invalid_self_report"

    def test_goal_with_zero_nodes_returns_done_summary(self, tmp_db):
        from src.data import database as db
        goal = db.create_goal("Empty Goal")
        from src.services.assessment import start_assessment
        result = start_assessment(goal=goal["id"], user_id="default", self_report=3)
        assert result["done"] is True
        assert result["probe"] is None
        assert result["total_nodes"] == 0
        assert result["summary"]["mastered"] == 0
        assert result["summary"]["unknown"] == 0
        assert result["summary"]["probes_done"] == 0
        assert result["summary"]["total_nodes"] == 0

    def test_resolve_goal_by_prefix(self, tmp_db, make_node, make_edge):
        _nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import start_assessment
        with patch("src.services.assessment.generate_probe_question", return_value=_probe_qa()):
            result = start_assessment(goal=goal["id"][:8], user_id="default", self_report=1)
        assert result["goal_id"] == goal["id"]
        assert result["done"] is False
        assert result["probe"] is not None

    def test_happy_path_first_probe(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import start_assessment
        with patch(
            "src.services.assessment.generate_probe_question",
            return_value=_probe_qa("What is Node0?", "Node0 def."),
        ):
            result = start_assessment(goal=goal["id"], user_id="default", self_report=1)
        assert result["done"] is False
        assert result["history"] == []
        assert result["total_nodes"] == 3
        probe = result["probe"]
        # self_report=1 -> shallowest node
        assert probe["node_id"] == nodes[0]["id"]
        assert probe["question"] == "What is Node0?"
        assert probe["expected_answer"] == "Node0 def."
        assert probe["q_num"] == 1
        assert "depth_level" in probe
        assert "mastery_threshold" in probe
        assert "strictness_level" in probe


# ── answer_probe ─────────────────────────────────────────────────────────────────

class TestAnswerProbe:
    def _start(self, goal, self_report=1):
        from src.services.assessment import start_assessment
        with patch("src.services.assessment.generate_probe_question", return_value=_probe_qa()):
            return start_assessment(goal=goal["id"], user_id="default", self_report=self_report)

    def test_pass_propagates_mastery_and_returns_next_probe(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.data import database as db
        from src.services.assessment import answer_probe
        started = self._start(goal, self_report=1)
        probe = started["probe"]
        with patch("src.services.assessment.score_answer", return_value=_score(0.95)), patch(
            "src.services.assessment.generate_probe_question", return_value=_probe_qa("Q2", "A2")
        ):
            result = answer_probe(
                goal=goal["id"],
                user_id="default",
                self_report=1,
                history=started["history"],
                probe=probe,
                user_answer="my answer",
            )
        # mastery persisted for probed node
        state = db.get_state(nodes[0]["id"], "default")
        assert state["status"] == "mastered"
        # history grows
        assert len(result["history"]) == 1
        assert result["history"][0]["node_id"] == nodes[0]["id"]
        assert result["history"][0]["passed"] is True
        # next probe returned
        assert result["done"] is False
        assert result["probe"] is not None
        assert result["probe"]["q_num"] == 2

    def test_partial_score_marks_learning_no_propagation(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.data import database as db
        from src.services.assessment import answer_probe
        started = self._start(goal, self_report=1)
        probe = started["probe"]
        with patch("src.services.assessment.score_answer", return_value=_score(0.6)), patch(
            "src.services.assessment.generate_probe_question", return_value=_probe_qa("Q2", "A2")
        ):
            result = answer_probe(
                goal=goal["id"], user_id="default", self_report=1,
                history=started["history"], probe=probe, user_answer="partial",
            )
        state = db.get_state(nodes[0]["id"], "default")
        assert state["status"] == "learning"
        assert result["history"][0]["passed"] is False

    def test_fail_propagates_unknown(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.data import database as db
        from src.services.assessment import answer_probe
        started = self._start(goal, self_report=1)
        probe = started["probe"]
        with patch("src.services.assessment.score_answer", return_value=_score(0.1)), patch(
            "src.services.assessment.generate_probe_question", return_value=_probe_qa("Q2", "A2")
        ):
            result = answer_probe(
                goal=goal["id"], user_id="default", self_report=1,
                history=started["history"], probe=probe, user_answer="wrong",
            )
        # probed node unknown
        assert db.get_state(nodes[0]["id"], "default")["status"] == "unknown"
        # dependents propagated to unknown
        assert db.get_state(nodes[1]["id"], "default")["status"] == "unknown"
        assert result["history"][0]["passed"] is False

    def test_empty_answer_is_scored_as_no_answer(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import answer_probe
        started = self._start(goal, self_report=1)
        probe = started["probe"]
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return _score(0.0)

        with patch("src.services.assessment.score_answer", side_effect=_capture), patch(
            "src.services.assessment.generate_probe_question", return_value=_probe_qa("Q2", "A2")
        ):
            answer_probe(
                goal=goal["id"], user_id="default", self_report=1,
                history=started["history"], probe=probe, user_answer="",
            )
        # empty answer is substituted, never an empty string into the scorer
        assert captured["user_answer"]
        assert captured["question"] == probe["question"]
        assert captured["expected_answer"] == probe["expected_answer"]

    def test_sequence_ends_in_summary(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import start_assessment, answer_probe
        from src.data import database as db

        with patch("src.services.assessment.generate_probe_question", return_value=_probe_qa()):
            state = start_assessment(goal=goal["id"], user_id="default", self_report=1)

        # Answer every probe as fail until the service finalizes.
        guard = 0
        while not state["done"]:
            guard += 1
            assert guard < 20
            probe = state["probe"]
            with patch("src.services.assessment.score_answer", return_value=_score(0.1)), patch(
                "src.services.assessment.generate_probe_question", return_value=_probe_qa()
            ):
                state = answer_probe(
                    goal=goal["id"], user_id="default", self_report=1,
                    history=state["history"], probe=probe, user_answer="x",
                )

        assert state["done"] is True
        summary = state["summary"]
        assert summary["total_nodes"] == 3
        assert summary["probes_done"] >= 1
        # every node now has a persisted state (untouched marked unknown)
        states = {s["node_id"] for s in db.list_states(user_id="default")}
        assert {n["id"] for n in nodes}.issubset(states)
        # recommended start node is a real unknown node id (or absent if all mastered)
        if summary.get("recommended_start_node"):
            assert summary["recommended_start_node"]["node_id"] in {n["id"] for n in nodes}

    def test_finalize_recommends_shallowest_unknown(self, tmp_db, make_node, make_edge):
        nodes, goal = _make_chain(make_node, make_edge, n=3)
        from src.services.assessment import start_assessment, answer_probe
        with patch("src.services.assessment.generate_probe_question", return_value=_probe_qa()):
            state = start_assessment(goal=goal["id"], user_id="default", self_report=1)
        guard = 0
        while not state["done"]:
            guard += 1
            assert guard < 20
            probe = state["probe"]
            with patch("src.services.assessment.score_answer", return_value=_score(0.1)), patch(
                "src.services.assessment.generate_probe_question", return_value=_probe_qa()
            ):
                state = answer_probe(
                    goal=goal["id"], user_id="default", self_report=1,
                    history=state["history"], probe=probe, user_answer="x",
                )
        rec = state["summary"]["recommended_start_node"]
        assert rec is not None
        # shallowest unknown is the depth=1 node
        assert rec["node_id"] == nodes[0]["id"]
