"""
File: tests/test_cross_pillar_smoke.py

Purpose:
    End-to-end SMOKE test for the three-pillar architecture (doc 18 P1). It
    walks a full learner journey — decompose → assess → learn → exam
    (generate / record / score) → review — against ONE real SQLite file, and
    asserts that data written by the generation plane (skill scripts + src
    services) is readable by BOTH data planes (CLI domain helpers + web
    workflow_api). Only the LLM boundary is mocked; the database is real.

Responsibilities:
    - Persist a decomposition through the skill's persist_result (generation).
    - Read the same graph back through the CLI data plane (src.domain.dag) and
      the web data plane (workflow_api "goals" / "graph").
    - Drive assessment / learning / exam services (LLM mocked) so each pillar's
      writes land in the shared DB.
    - Verify the web data plane (exam_get / exam_record_answer) sees the
      tool-generated exam, never leaking expected_answer (doc 16), and that the
      tool's scoring + finalize schedules a review the CLI/web queue can read.

What this file does NOT do:
    - Make real LLM calls (all agent generation/scoring functions are patched).
    - Re-test each service's internal branching (covered by the per-service
      unit tests). This is a contract/smoke test of the shared-DB seams only.
    - Exercise the Node server or the browser; it calls workflow_api in-process.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERSIST_SCRIPT = ROOT / "skills" / "decompose-learning-goal" / "scripts" / "persist_result.py"
SERVE_SCRIPTS = ROOT / "skills" / "serve-learning-graph" / "scripts"

USER = "smoke-user"


# ── Module loaders (skill scripts live outside the importable package) ──────────

def _load_persist_module():
    spec = importlib.util.spec_from_file_location("smoke_persist_result", PERSIST_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_workflow_module():
    # workflow_api does `from export_graph import ...`, so its scripts dir must be importable.
    if str(SERVE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SERVE_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "smoke_workflow_api", SERVE_SCRIPTS / "workflow_api.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Generation-plane fixtures: a small reviewed decomposition + LLM mock shapes ─

def _sample_decomposition() -> dict:
    """Two atomic nodes under one parent, with a prerequisite edge between them."""
    return {
        "target": "Learn Kubernetes service networking",
        "assumptions": ["Learner already understands basic TCP/IP."],
        "sources": [],
        "nodes": [
            {
                "title": "Kubernetes service networking",
                "description": "Stable Service access to changing Pod backends.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["stable abstraction"],
                "difficulty": 3,
                "est_minutes": 0,
                "strictness_level": "standard",
                "mastery_threshold": 0.8,
                "risk_note": "",
                "is_atomic": False,
                "parent_title": None,
                "qa_draft": [],
            },
            {
                "title": "ClusterIP service model",
                "description": "Stable virtual IPs for Services.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["indirection"],
                "difficulty": 2,
                "est_minutes": 12,
                "strictness_level": "standard",
                "mastery_threshold": 0.8,
                "risk_note": "",
                "is_atomic": True,
                "parent_title": "Kubernetes service networking",
                "qa_draft": ["What problem does ClusterIP solve?"],
            },
            {
                "title": "EndpointSlice mapping",
                "description": "How EndpointSlices represent Service backends.",
                "domain": "Kubernetes",
                "concept_fingerprint": ["dynamic membership"],
                "difficulty": 3,
                "est_minutes": 15,
                "strictness_level": "standard",
                "mastery_threshold": 0.8,
                "risk_note": "",
                "is_atomic": True,
                "parent_title": "Kubernetes service networking",
                "qa_draft": ["What does an EndpointSlice store?"],
            },
        ],
        "edges": [
            {
                "from_title": "ClusterIP service model",
                "to_title": "EndpointSlice mapping",
                "edge_type": "prerequisite",
                "weight": 1.0,
                "analogy_desc": None,
            }
        ],
        "unresolved_questions": [],
    }


def _score(score, error_type=None, explanation="ok", related=None):
    return {
        "score": score,
        "error_type": error_type,
        "explanation": explanation,
        "related_concepts": related or [],
    }


def _questions(n=2):
    return [
        {
            "question_type": "short_answer",
            "question": f"Question {i + 1}?",
            "options": None,
            "expected_answer": f"Expected answer {i + 1}.",
            "source_section": (i + 1) if i == 0 else None,
            "is_expansion": i != 0,
        }
        for i in range(n)
    ]


def _outline_sections(n=2):
    return [
        {
            "index": i + 1,
            "title": f"Section {i + 1}",
            "content": f"Content {i + 1}",
            "needs_search": False,
            "sources": [],
            "analogy": None,
            "analogy_source_node": None,
            "covered": False,
        }
        for i in range(n)
    ]


def _fake_generate_outline(node_id, sections):
    """Mock for teacher.generate_outline: persists a real validated outline."""
    from src.data import database as db

    def _gen(*args, **kwargs):
        outline = db.create_outline(
            node_id=node_id, sections=sections, user_id=kwargs.get("user_id", USER)
        )
        db.update_outline(outline["id"], status="validated")
        outline["status"] = "validated"
        outline["sections"] = sections
        return outline

    return _gen


# ── The journey ─────────────────────────────────────────────────────────────────

def test_full_journey_shares_one_db(tmp_db):
    """
    One SQLite file, every pillar in turn. Each stage reads what the previous
    stage wrote, proving the shared-DB contract across the three pillars.
    """
    from src.data import database as db

    db_file = str(tmp_db)  # same path config.DB_PATH is patched to by tmp_db
    persist = _load_persist_module()
    workflow = _load_workflow_module()

    # ── 1. GENERATION: decompose persisted via the skill script ────────────────
    summary = persist.persist_result(_sample_decomposition(), db_path=db_file, user_id=USER)
    goal_id = summary["goal_id"]
    assert summary["nodes"] == 3 and summary["edges"] == 1

    atomic = db.list_nodes_for_goal(goal_id, atomic_only=True)
    titles = {n["title"]: n for n in atomic}
    assert set(titles) == {"ClusterIP service model", "EndpointSlice mapping"}

    # ── 2. DATA PLANE (CLI domain): topological order honors the prerequisite ──
    from src.domain import dag

    order = [n["title"] for n in dag.topological_order(goal_id)]
    assert order.index("ClusterIP service model") < order.index("EndpointSlice mapping")

    # ── 3. DATA PLANE (web): goals + graph read the same persisted data ────────
    goals_resp = workflow.handle_action("goals", db_path=db_file, user_id=USER)
    assert any(g["id"] == goal_id for g in goals_resp["goals"])

    graph_resp = workflow.handle_action(
        "graph", db_path=db_file, user_id=USER, payload={"goal": goal_id}
    )
    graph_titles = {n.get("title") or n.get("label") for n in graph_resp["graph"]["nodes"]}
    assert "ClusterIP service model" in graph_titles

    # ── 4. GENERATION: assessment writes user_knowledge_state (LLM mocked) ──────
    from src.services import assessment

    with patch(
        "src.services.assessment.generate_probe_question",
        return_value={"question": "Probe?", "expected_answer": "Ans."},
    ):
        started = assessment.start_assessment(goal=goal_id, user_id=USER, self_report=3)
    assert started["probe"] is not None

    with patch("src.services.assessment.score_answer", return_value=_score(0.95)), patch(
        "src.services.assessment.generate_probe_question",
        return_value={"question": "Probe2?", "expected_answer": "Ans2."},
    ):
        assessment.answer_probe(
            goal=goal_id,
            user_id=USER,
            self_report=3,
            history=started["history"],
            probe=started["probe"],
            user_answer="my answer",
        )
    assert db.list_states(user_id=USER), "assessment should have written knowledge state"

    # ── 5. GENERATION: learn writes outline + session + chat history ───────────
    node = titles["ClusterIP service model"]
    node_id = node["id"]
    sections = _outline_sections(2)

    from src.services import learning

    with patch("src.services.learning.generate_outline", _fake_generate_outline(node_id, sections)):
        prep = learning.prepare_node(node=node_id, user_id=USER)
    session_id = prep["session"]["id"]
    assert prep["progress"] == 0.0

    def _fake_chat_turn(session, node, outline_sections, user_message, history, model=None):
        db.add_chat_message(session["id"], role="user", content=user_message)
        db.add_chat_message(session["id"], role="assistant", content="reply!")
        db.update_session(session["id"], covered_sections=[1, 2], progress=1.0)
        return "reply!", 1.0, [1, 2]

    with patch("src.services.learning.chat_turn", _fake_chat_turn):
        msg = learning.send_message(session_id=session_id, user_id=USER, message="explain")
    assert msg["response"] == "reply!"
    assert len(db.get_chat_history(session_id)) == 2

    # ── 6. GENERATION: exam generated by the tool (reuses the learn outline) ───
    from src.services import exam

    with patch("src.services.exam.generate_questions", return_value=_questions(2)):
        started_exam = exam.start_exam(node=node_id, user_id=USER)
    exam_id = started_exam["exam_id"]
    assert started_exam["total"] == 2
    for q in started_exam["questions"]:
        assert "expected_answer" not in q  # never leaked to the client

    # ── 6a. DATA PLANE (web): exam_get shows questions, no answer key ──────────
    eg = workflow.handle_action(
        "exam_get", db_path=db_file, user_id=USER, payload={"exam": exam_id}
    )
    assert eg["total"] == 2
    for q in eg["questions"]:
        assert "expected_answer" not in q
        assert q["score"] is None  # not scored yet

    # ── 6b. DATA PLANE (web): learner records raw answers (score stays NULL) ───
    for q in eg["questions"]:
        workflow.handle_action(
            "exam_record_answer",
            db_path=db_file,
            user_id=USER,
            payload={"exam": exam_id, "question_id": q["id"], "user_answer": "learner answer"},
        )
    after_record = workflow.handle_action(
        "exam_get", db_path=db_file, user_id=USER, payload={"exam": exam_id}
    )
    assert all(q["user_answer"] for q in after_record["questions"])
    assert all(q["score"] is None for q in after_record["questions"])

    # ── 6c. GENERATION: the tool scores every answer, then finalizes ───────────
    rows = db.get_exam_questions(exam_id)
    with patch("src.services.exam.score_answer", return_value=_score(1.0)):
        for row in rows:
            exam.answer_question(exam_id, row["id"], USER, "learner answer")
    result = exam.finish_exam(exam_id, user_id=USER)
    assert result["passed"] is True

    # finalize must have updated the node's mastery state ...
    state = db.get_state(node_id, USER)
    assert state and state["status"] == "mastered"

    # ── 7. DATA PLANE: review queue sees the review the exam scheduled ─────────
    from src.services import review

    queue = review.get_queue(user_id=USER, include_future=True)
    queued_titles = {
        v["node_title"] for group in queue["groups"].values() for v in group
    }
    assert queue["total"] >= 1
    assert "ClusterIP service model" in queued_titles
