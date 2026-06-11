"""
Tests for the decompose-learning-goal Codex skill artifact.

Purpose:
    Verify that the project contains a usable, project-local Codex skill for
    recursive learning goal decomposition.

Responsibilities:
    - Assert the skill has the required Codex file structure
    - Assert the skill documents the agreed workflow boundaries
    - Assert the example JSON follows the published review contract
    - Assert the persistence workflow is documented and script-backed
    - Assert the project docs index includes the skill design document

What this file does NOT do:
    - Execute Codex or Claude subagents
    - Validate live web search behavior
    - Validate SQLite persistence behavior; see test_decompose_skill_persistence.py
"""

import json
import re
from enum import Enum
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "decompose-learning-goal"


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ReviewProvider(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    NONE = "none"


def _read_skill_file(relative_path: str) -> str:
    return (SKILL_ROOT / relative_path).read_text(encoding="utf-8")


def _example_json() -> dict:
    example = _read_skill_file("references/example-output.md")
    match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", example)
    assert match, "example-output.md must contain a fenced JSON object"
    return json.loads(match.group(1))


def _enum_values(enum_cls: type[Enum]) -> set[str]:
    return {item.value for item in enum_cls}


def test_skill_files_exist():
    assert (SKILL_ROOT / "SKILL.md").exists()
    assert (SKILL_ROOT / "agents" / "openai.yaml").exists()
    assert (SKILL_ROOT / "scripts" / "persist_result.py").exists()
    assert (SKILL_ROOT / "references" / "output-schema.md").exists()
    assert (SKILL_ROOT / "references" / "review-checklist.md").exists()
    assert (SKILL_ROOT / "references" / "example-output.md").exists()


def test_skill_frontmatter_and_trigger_description():
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "name: decompose-learning-goal" in text
    assert "description:" in text
    assert "recursive learning goal decomposition" in text
    assert "subagent" in text
    assert "web search" in text
    assert "SQLite persistence" in text


def test_skill_workflow_captures_confirmed_design():
    text = _read_skill_file("SKILL.md")

    required_phrases = [
        "Use available subagent capability",
        "Local batch review",
        "Global DAG review",
        "Codex subagent",
        "Claude Code Task",
        "Search only when",
        "Output both",
        "scripts/persist_result.py",
        "current execution",
        "learning.db",
        "Do not generate SQL",
        "If no subagent capability is available",
        "ReviewStatus.SKIPPED",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_skill_documents_deterministic_persistence():
    skill = _read_skill_file("SKILL.md")
    schema = _read_skill_file("references/output-schema.md")
    script = _read_skill_file("scripts/persist_result.py")

    for text in (skill, schema):
        assert "scripts/persist_result.py" in text
        assert "learning.db" in text
        assert "DB_PATH" in text
        assert "Do not generate SQL" in text or "must not\ngenerate SQL" in text

    assert "SCHEMA_SQL as CLI_SCHEMA_SQL" in script
    assert "SKILL_IMPORT_SCHEMA_SQL" in script
    assert "UNIQUE(user_id, content_hash)" in script
    assert "ON DELETE CASCADE" in script
    assert "VALID_EDGE_TYPES" in script
    assert "prerequisite cycle" in script
    assert "skill_result_imports" in script
    assert "skill_result_sources" in script
    assert "resolve_db_path" in script


def test_output_schema_matches_project_data_model():
    schema = _read_skill_file("references/output-schema.md")

    for field in [
        "target",
        "assumptions",
        "sources",
        "nodes",
        "edges",
        "review",
        "unresolved_questions",
    ]:
        assert field in schema

    for node_field in [
        "concept_fingerprint",
        "strictness_level",
        "mastery_threshold",
        "qa_draft",
        "is_atomic",
    ]:
        assert node_field in schema

    assert "ReviewStatus" in schema
    assert "ReviewProvider" in schema
    assert "local_checks" in schema
    assert "global_check" in schema
    assert "parent_title" in schema
    assert "Exactly one" in schema or "exactly one root node" in schema
    assert "learning_goals.root_node" in schema
    assert "knowledge_nodes" in schema
    assert "knowledge_edges" in schema
    assert "prerequisite" in schema
    assert "acyclic" in schema
    for status in ReviewStatus:
        assert status.value in schema
    for provider in ReviewProvider:
        assert provider.value in schema
    assert "unavailable" not in schema


def test_skill_requires_enum_selection_during_use():
    skill = _read_skill_file("SKILL.md")
    schema = _read_skill_file("references/output-schema.md")

    for text in (skill, schema):
        assert "choose a ReviewStatus enum member" in text
        assert "choose a ReviewProvider enum member" in text
        assert "serialize the enum values" in text


def test_example_output_json_matches_review_contract():
    data = _example_json()

    for field in [
        "target",
        "assumptions",
        "sources",
        "nodes",
        "edges",
        "review",
        "unresolved_questions",
    ]:
        assert field in data

    review = data["review"]
    assert review["status"] in _enum_values(ReviewStatus)
    assert review["provider"] in _enum_values(ReviewProvider)
    assert review["status"] == ReviewStatus.APPROVED.value
    assert review["provider"] in {
        ReviewProvider.CODEX.value,
        ReviewProvider.CLAUDE.value,
    }
    assert review["local_checks"]
    assert review["global_check"]["status"] in _enum_values(ReviewStatus)


def test_example_output_review_has_local_and_global_checks():
    data = _example_json()
    review = data["review"]
    node_titles = {node["title"] for node in data["nodes"]}

    local_checks = review["local_checks"]
    assert len(local_checks) >= 1
    for check in local_checks:
        assert check["parent_title"] in node_titles
        assert check["status"] in _enum_values(ReviewStatus)
        assert check["provider"] in _enum_values(ReviewProvider)
        assert isinstance(check["issues"], list)
        assert isinstance(check["suggestions_applied"], list)

    global_check = review["global_check"]
    assert global_check["status"] in _enum_values(ReviewStatus)
    assert global_check["provider"] in _enum_values(ReviewProvider)
    assert isinstance(global_check["issues"], list)
    assert isinstance(global_check["suggestions_applied"], list)


def test_example_output_has_consistent_nodes_and_edges():
    data = _example_json()
    nodes = data["nodes"]
    edges = data["edges"]
    titles = {node["title"] for node in nodes}

    atomic_nodes = [node for node in nodes if node["is_atomic"]]
    assert len(atomic_nodes) >= 3
    assert len(edges) >= 2

    for node in atomic_nodes:
        assert len(node["qa_draft"]) >= 3

    prerequisite_edges = [edge for edge in edges if edge["edge_type"] == "prerequisite"]
    assert len(prerequisite_edges) >= 2
    for edge in prerequisite_edges:
        assert edge["from_title"] in titles
        assert edge["to_title"] in titles

    assert "EndpointSlice mapping" in titles
    assert any("EndpointSlice" in item for item in data["review"]["suggestions_applied"])


def test_docs_summary_links_skill_design_doc():
    summary = (ROOT / "docs" / "summary.md").read_text(encoding="utf-8")

    assert "11" in summary
    assert "decompose-learning-goal-skill" in summary
    assert "独立 agent skill" in summary
    assert "SQLite persistence" in summary
