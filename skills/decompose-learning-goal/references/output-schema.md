# Output Schema

Return two sections:

1. A concise human-readable decomposition summary.
2. A fenced JSON block with this shape.

```json
{
  "target": "string",
  "assumptions": ["string"],
  "sources": [
    {
      "title": "string",
      "url": "string",
      "used_for": "string"
    }
  ],
  "nodes": [
    {
      "title": "string",
      "description": "string",
      "domain": "string",
      "concept_fingerprint": ["string"],
      "difficulty": 1,
      "est_minutes": 15,
      "strictness_level": "critical | standard | familiarity",
      "mastery_threshold": 0.8,
      "risk_note": "string",
      "is_atomic": true,
      "parent_title": "string | null",
      "qa_draft": [
        "question 1",
        "question 2",
        "question 3"
      ]
    }
  ],
  "edges": [
    {
      "from_title": "source prerequisite node title",
      "to_title": "target dependent node title",
      "edge_type": "prerequisite | cross_domain_analogy",
      "weight": 1.0,
      "analogy_desc": "string | null"
    }
  ],
  "review": {
    "status": "approved",
    "provider": "codex",
    "local_checks": [
      {
        "parent_title": "string",
        "status": "approved",
        "provider": "codex",
        "issues": ["string"],
        "suggestions_applied": ["string"]
      }
    ],
    "global_check": {
      "status": "approved",
      "provider": "codex",
      "issues": ["string"],
      "suggestions_applied": ["string"]
    },
    "issues": ["string"],
    "suggestions_applied": ["string"]
  },
  "unresolved_questions": ["string"]
}
```

## Field Rules

- `target`: The original user goal after any explicit narrowing.
- `assumptions`: Important assumptions made because the user did not specify details.
- `sources`: Empty when no web search was needed. Include only sources actually used.
- `nodes`: Include grouping nodes and atomic leaf nodes. Persistence-ready output
  must contain exactly one root node with `parent_title: null`; this root maps to
  `learning_goals.root_node`.
- `concept_fingerprint`: 2-3 abstract tags for cross-domain analogy.
- `strictness_level`: One of `critical`, `standard`, or `familiarity`.
- `mastery_threshold`: Use `0.95` for critical, `0.80` for standard, `0.60` for familiarity.
- `qa_draft`: Required for atomic nodes; optional empty list for non-atomic nodes.
- `edges`: Use title references so the JSON stays portable before database IDs exist.

## Persistence Mapping

Persist the final JSON with `scripts/persist_result.py`. The model must not
generate SQL. The script defines the database creation, indexing, validation,
and inserts in code.

DB path rules:

- No DB path from the user: current execution project root / `learning.db`.
- Directory from the user: that directory / `learning.db`.
- `.db`, `.sqlite`, or `.sqlite3` file from the user: that exact file.
- The current CLI reads `src.infrastructure.config.DB_PATH`; when this differs from the
  persisted DB path, run the CLI with `DB_PATH=<db_path>` or persist with
  `--db data/learning.db`.

SQLite mapping follows the current CLI data model:

- `target` -> `learning_goals.title`.
- Exactly one node with `parent_title: null` -> `learning_goals.root_node`.
- `nodes[]` -> `knowledge_nodes`.
- `nodes[].parent_title` -> `knowledge_nodes.parent_node` after title-to-ID resolution.
- `nodes[].qa_draft[]` -> `knowledge_nodes.qa_set[]` with empty `expected_answer`
  and the node difficulty.
- `edges[].from_title` / `edges[].to_title` -> `knowledge_edges.from_node` /
  `knowledge_edges.to_node` after title-to-ID resolution.
- `assumptions`, `review`, `sources`, `unresolved_questions`, and the raw JSON
  -> skill import metadata tables, not the CLI graph tables.

Persistence requires every `parent_title`, `from_title`, and `to_title` to match
an existing node title exactly. It also requires:

- `edge_type` to be `prerequisite` or `cross_domain_analogy`.
- `prerequisite` edges to connect atomic nodes only.
- `prerequisite` edges to form an acyclic atomic learning order.
- identical JSON imports to be idempotent per `user_id`, not globally.
## Enums

`ReviewStatus`:

- `APPROVED = "approved"`: Independent subagent review passed.
- `REJECTED = "rejected"`: Independent subagent review found unresolved structural issues.
- `SKIPPED = "skipped"`: No subagent capability is available in the current tool.

`ReviewProvider`:

- `CODEX = "codex"`: Review used a Codex subagent.
- `CLAUDE = "claude"`: Review used a Claude Code Task/subagent.
- `NONE = "none"`: Review was skipped; only valid with `ReviewStatus.SKIPPED`.

## Review Rules

- During use, choose a ReviewStatus enum member and choose a ReviewProvider enum member first, then serialize the enum values into JSON.
- `review.status`: Must be a `ReviewStatus` enum value.
- `review.provider`: Must be a `ReviewProvider` enum value.
- `review.local_checks`: One entry per reviewed parent-to-children decomposition batch.
  Do not create one local check per individual node.
- `review.local_checks[].parent_title`: The non-atomic parent whose children were
  reviewed as a batch.
- `review.global_check`: Final whole-DAG review for cycles, missing dependencies,
  duplicate nodes, and import readiness.
- `review.issues`: When status is `skipped`, include an explicit issue such as
  "No subagent capability was available; independent review was skipped."
- `unresolved_questions`: Keep any high-impact uncertainty here instead of hiding it.
