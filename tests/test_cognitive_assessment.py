"""
tests/test_cognitive_assessment.py

Unit tests for the cognitive preference assessment module.
Tests the question generation, answer scoring, and weight computation
used to determine a user's mnemonic strategy preference.

All LLM calls are mocked.
"""

import pytest
from unittest.mock import patch


# ── Assessment Questions ──────────────────────────────────────────────────────

class TestAssessmentQuestions:
    def test_get_assessment_questions_returns_list(self):
        from src.agents.mnemonic import get_assessment_questions
        questions = get_assessment_questions()
        assert isinstance(questions, list)
        assert len(questions) >= 3  # at least 3 questions

    def test_each_question_has_required_fields(self):
        from src.agents.mnemonic import get_assessment_questions
        questions = get_assessment_questions()
        for q in questions:
            assert "prompt" in q
            assert "options" in q
            assert isinstance(q["options"], list)
            assert len(q["options"]) == 3  # one per strategy
            for opt in q["options"]:
                assert "label" in opt
                assert "strategy" in opt
                assert opt["strategy"] in ("spatial", "symbolic", "narrative")

    def test_each_question_covers_all_strategies(self):
        from src.agents.mnemonic import get_assessment_questions
        questions = get_assessment_questions()
        for q in questions:
            strategies = {opt["strategy"] for opt in q["options"]}
            assert strategies == {"spatial", "symbolic", "narrative"}


# ── Weight Computation ────────────────────────────────────────────────────────

class TestComputeWeights:
    def test_all_spatial_answers(self):
        from src.agents.mnemonic import compute_weights
        answers = ["spatial", "spatial", "spatial", "spatial"]
        weights = compute_weights(answers)
        assert weights["spatial"] > weights["symbolic"]
        assert weights["spatial"] > weights["narrative"]
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_all_symbolic_answers(self):
        from src.agents.mnemonic import compute_weights
        answers = ["symbolic", "symbolic", "symbolic", "symbolic"]
        weights = compute_weights(answers)
        assert weights["symbolic"] > weights["spatial"]
        assert weights["symbolic"] > weights["narrative"]

    def test_all_narrative_answers(self):
        from src.agents.mnemonic import compute_weights
        answers = ["narrative", "narrative", "narrative", "narrative"]
        weights = compute_weights(answers)
        assert weights["narrative"] > weights["spatial"]
        assert weights["narrative"] > weights["symbolic"]

    def test_mixed_answers(self):
        from src.agents.mnemonic import compute_weights
        answers = ["spatial", "symbolic", "spatial", "narrative"]
        weights = compute_weights(answers)
        assert weights["spatial"] > weights["narrative"]
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_empty_answers_returns_uniform(self):
        from src.agents.mnemonic import compute_weights
        weights = compute_weights([])
        assert abs(weights["spatial"] - 0.33) < 0.02
        assert abs(weights["symbolic"] - 0.33) < 0.02
        assert abs(weights["narrative"] - 0.34) < 0.02

    def test_weights_always_sum_to_one(self):
        from src.agents.mnemonic import compute_weights
        for combo in [
            ["spatial"], ["symbolic", "narrative"],
            ["spatial", "spatial", "symbolic", "narrative"],
        ]:
            weights = compute_weights(combo)
            assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_weights_have_minimum_floor(self):
        """Even dominant preference should leave small weight for other strategies."""
        from src.agents.mnemonic import compute_weights
        answers = ["spatial", "spatial", "spatial", "spatial"]
        weights = compute_weights(answers)
        # Non-dominant strategies should still get at least a small weight
        assert weights["symbolic"] >= 0.05
        assert weights["narrative"] >= 0.05


# ── Dominant Strategy ─────────────────────────────────────────────────────────

class TestDominantStrategy:
    def test_get_dominant_strategy(self):
        from src.agents.mnemonic import get_dominant_strategy
        weights = {"spatial": 0.6, "symbolic": 0.3, "narrative": 0.1}
        assert get_dominant_strategy(weights) == "spatial"

    def test_get_dominant_strategy_symbolic(self):
        from src.agents.mnemonic import get_dominant_strategy
        weights = {"spatial": 0.2, "symbolic": 0.5, "narrative": 0.3}
        assert get_dominant_strategy(weights) == "symbolic"

    def test_get_dominant_strategy_narrative(self):
        from src.agents.mnemonic import get_dominant_strategy
        weights = {"spatial": 0.1, "symbolic": 0.2, "narrative": 0.7}
        assert get_dominant_strategy(weights) == "narrative"


# ── Mnemonic Prompt Builder ───────────────────────────────────────────────────

class TestMnemonicPromptSnippet:
    def test_spatial_prompt_snippet(self):
        from src.agents.mnemonic import build_mnemonic_prompt_snippet
        snippet = build_mnemonic_prompt_snippet("spatial")
        assert "空间" in snippet or "场景" in snippet or "位置" in snippet
        assert len(snippet) > 20

    def test_symbolic_prompt_snippet(self):
        from src.agents.mnemonic import build_mnemonic_prompt_snippet
        snippet = build_mnemonic_prompt_snippet("symbolic")
        assert "规则" in snippet or "逻辑" in snippet or "分类" in snippet
        assert len(snippet) > 20

    def test_narrative_prompt_snippet(self):
        from src.agents.mnemonic import build_mnemonic_prompt_snippet
        snippet = build_mnemonic_prompt_snippet("narrative")
        assert "故事" in snippet or "叙事" in snippet or "情节" in snippet
        assert len(snippet) > 20

    def test_unknown_strategy_returns_empty(self):
        from src.agents.mnemonic import build_mnemonic_prompt_snippet
        snippet = build_mnemonic_prompt_snippet("unknown")
        assert snippet == ""
