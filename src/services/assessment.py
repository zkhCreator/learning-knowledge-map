"""
File: src/services/assessment.py

Purpose:
    Stateless, non-interactive goal-assessment service. This is the HTTP-facing
    decomposition of agents.assessor.run_assessment_loop: it exposes the same
    adaptive probing logic as two pure request/response steps instead of an
    in-memory REPL.

Responsibilities:
    - Resolve a goal by full id or prefix (mirrors the CLI _resolve_goal rule).
    - Generate the first probe question for a new assessment (start_assessment).
    - Score a carried probe answer, apply the loop's mastery/learning/unknown
      branching, persist durable state, and compute the next probe or the final
      summary (answer_probe).
    - Reuse the pure functions in agents.assessor / agents.examiner — it does NOT
      reimplement probe selection, question generation, scoring, or propagation.

What this file does NOT do:
    - Hold any server-side memory between calls. Each call is a fresh process;
      the in-flight state (history + carried probe context, incl. expected_answer)
      is returned to and re-supplied by the client. Only durable results are
      written to user_knowledge_state via db.upsert_state.
    - Call input() / Rich / console output.
    - Expose run_assessment_loop over HTTP.
    - Add DB tables or schema changes.

Key Design Decisions:
    - The probe dict carries question + expected_answer so the next request can
      score without re-generating. Acceptable for this local single-user tool.
    - Every public function takes explicit user_id + goal id (doc/14 rule).
    - Branching mirrors run_assessment_loop steps 2-4:
        score >= node mastery_threshold -> _propagate_mastery
        0.5 <= score < threshold        -> upsert_state(status="learning")
        score < 0.5                     -> _propagate_unknown
      "passed" in history means score >= threshold (drives next_probe_node).
    - Finalization marks every still-untouched node as unknown and builds a
      JSON-able summary, including the shallowest unknown node as the
      recommended start node. It never auto-starts learning (doc/14 独立路径).

Inputs:
    - goal: goal id or prefix; user_id: learner id; self_report: int 1-5
    - history: list of {node_id, depth_level, score, passed} (client-carried)
    - probe: the carried probe context dict (client-carried)
    - user_answer: free-text answer to the carried probe

Outputs:
    - start_assessment -> {goal_id, goal_title, self_report, total_nodes,
                           history, probe|null, done, summary?}
    - answer_probe     -> {done, history, probe?, summary?}
"""

from __future__ import annotations

from typing import Any, Optional

from src.agents.assessor import (
    generate_probe_question,
    next_probe_node,
    _propagate_mastery,
    _propagate_unknown,
)
from src.agents.examiner import score_answer
from src.data import database as db
from src.infrastructure.logger import get_logger

log = get_logger(__name__)

PARTIAL_THRESHOLD = 0.5  # >= this but below mastery_threshold => "learning"


class AssessmentError(ValueError):
    """Raised for expected assessment errors that should become JSON envelopes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Goal / input resolution ──────────────────────────────────────────────────────

def _resolve_goal(goal: str, user_id: str) -> dict:
    """Resolve a goal by full id or prefix; mirror the CLI resolution rules."""
    if not goal:
        raise AssessmentError("goal_not_found", "No goal id or prefix provided.")
    direct = db.get_goal(goal)
    if direct and direct.get("user_id", user_id) == user_id:
        return direct
    matches = [g for g in db.list_goals(user_id=user_id) if g["id"].startswith(goal)]
    if not matches:
        raise AssessmentError("goal_not_found", f"No goal id starting with '{goal}'.")
    if len(matches) > 1:
        raise AssessmentError("ambiguous_goal", f"Multiple goals match '{goal}'; provide a longer id.")
    return matches[0]


def _validate_self_report(self_report: Any) -> int:
    try:
        value = int(self_report)
    except (TypeError, ValueError) as exc:
        raise AssessmentError("invalid_self_report", "self_report must be an integer 1-5.") from exc
    if not 1 <= value <= 5:
        raise AssessmentError("invalid_self_report", "self_report must be between 1 and 5.")
    return value


# ── Probe context ────────────────────────────────────────────────────────────────

def _build_probe(node: dict, qa: dict, q_num: int) -> dict:
    """Build the JSON-able probe context the client carries to the next call."""
    return {
        "node_id": node["id"],
        "title": node["title"],
        "depth_level": node.get("depth_level", 0),
        "strictness_level": node.get("strictness_level", "standard"),
        "mastery_threshold": node.get("mastery_threshold", 0.80),
        "question": qa["question"],
        "expected_answer": qa["expected_answer"],
        "q_num": q_num,
    }


def _generate_probe(goal_id: str, history: list[dict], self_report: int,
                    model: Optional[str]) -> Optional[dict]:
    """Select the next probe node and generate its question, or None when done."""
    node = next_probe_node(goal_id, history=history, self_report=self_report)
    if node is None:
        return None
    qa = generate_probe_question(node, model=model)
    return _build_probe(node, qa, q_num=len(history) + 1)


# ── Finalization ─────────────────────────────────────────────────────────────────

def _finalize(goal_id: str, user_id: str, history: list[dict]) -> dict:
    """
    Mark every still-untouched node as unknown and build the summary.

    Mirrors run_assessment_loop steps 3-4 without any console output.
    """
    all_nodes = db.list_nodes_for_goal(goal_id, atomic_only=True)
    all_node_ids = {n["id"] for n in all_nodes}

    existing = {s["node_id"] for s in db.list_states(user_id=user_id)}
    for nid in all_node_ids - existing:
        db.upsert_state(node_id=nid, status="unknown", raw_score=0.0,
                        stability=1.0, user_id=user_id)

    final_states = db.list_states(user_id=user_id)
    state_map = {s["node_id"]: s for s in final_states if s["node_id"] in all_node_ids}
    mastered = sum(1 for s in state_map.values() if s["status"] == "mastered")
    unknown = sum(1 for s in state_map.values() if s["status"] != "mastered")

    summary = {
        "mastered": mastered,
        "unknown": unknown,
        "probes_done": len(history),
        "total_nodes": len(all_nodes),
        "recommended_start_node": None,
    }

    unknown_nodes = [
        n for n in all_nodes
        if state_map.get(n["id"], {}).get("status", "unknown") != "mastered"
    ]
    if unknown_nodes:
        unknown_nodes.sort(key=lambda n: n.get("depth_level", 0))
        first = unknown_nodes[0]
        summary["recommended_start_node"] = {
            "node_id": first["id"],
            "title": first["title"],
            "depth_level": first.get("depth_level", 0),
        }

    return summary


# ── Public API ───────────────────────────────────────────────────────────────────

def start_assessment(goal: str, user_id: str = "default", self_report: int = 3,
                     model: Optional[str] = None) -> dict:
    """
    Begin an assessment: resolve the goal and generate the first probe.

    Returns a JSON-able dict; the client must carry `history` and `probe` back
    into answer_probe. If the goal has no atomic nodes, returns a clean done
    result with a zeroed summary.
    """
    self_report = _validate_self_report(self_report)
    goal_row = _resolve_goal(goal, user_id)
    goal_id = goal_row["id"]

    all_nodes = db.list_nodes_for_goal(goal_id, atomic_only=True)
    total_nodes = len(all_nodes)
    base = {
        "goal_id": goal_id,
        "goal_title": goal_row.get("title", goal_id),
        "self_report": self_report,
        "total_nodes": total_nodes,
        "history": [],
    }

    if total_nodes == 0:
        return {
            **base,
            "probe": None,
            "done": True,
            "summary": {
                "mastered": 0,
                "unknown": 0,
                "probes_done": 0,
                "total_nodes": 0,
                "recommended_start_node": None,
            },
        }

    probe = _generate_probe(goal_id, history=[], self_report=self_report, model=model)
    if probe is None:
        return {**base, "probe": None, "done": True,
                "summary": _finalize(goal_id, user_id, history=[])}

    return {**base, "probe": probe, "done": False}


def answer_probe(goal: str, user_id: str, self_report: int,
                 history: list[dict], probe: dict, user_answer: str,
                 model: Optional[str] = None) -> dict:
    """
    Score a carried probe answer, apply branching, persist state, and return
    the next probe or the final summary.

    history/probe are client-carried in-flight state; only durable results are
    written via db.upsert_state / propagation.
    """
    self_report = _validate_self_report(self_report)
    goal_row = _resolve_goal(goal, user_id)
    goal_id = goal_row["id"]

    if not probe or not probe.get("node_id"):
        raise AssessmentError("invalid_probe", "Missing or malformed probe context.")
    history = list(history or [])

    node_id = probe["node_id"]
    threshold = probe.get("mastery_threshold", 0.80)
    answer = (user_answer or "").strip() or "（未作答）"

    scoring = score_answer(
        question=probe.get("question", ""),
        expected_answer=probe.get("expected_answer", ""),
        user_answer=answer,
        strictness=probe.get("strictness_level", "standard"),
        model=model,
    )
    score_val = float(scoring["score"])
    passed = score_val >= threshold

    if passed:
        _propagate_mastery(node_id, user_id=user_id, inferred_score=score_val)
    elif score_val >= PARTIAL_THRESHOLD:
        db.upsert_state(node_id=node_id, status="learning", raw_score=score_val,
                        stability=1.0, user_id=user_id)
    else:
        _propagate_unknown(node_id, user_id=user_id)

    history.append({
        "node_id": node_id,
        "depth_level": probe.get("depth_level", 0),
        "score": score_val,
        "passed": passed,
    })

    next_probe = _generate_probe(goal_id, history=history, self_report=self_report, model=model)
    if next_probe is None:
        return {"done": True, "history": history,
                "summary": _finalize(goal_id, user_id, history=history)}

    return {"done": False, "history": history, "probe": next_probe}
