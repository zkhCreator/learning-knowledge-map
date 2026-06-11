---
name: decompose-learning-goal
description: recursive learning goal decomposition into DAG-ready atomic knowledge nodes with dependency edges, source-aware web search, independent review using the current tool's available subagent capability, and deterministic SQLite persistence for Learning Directed Graph. Use when a user asks to break down a learning goal, design a learning graph, create prerequisite knowledge nodes, validate decomposition quality, persist the result for indexing, or produce both readable and machine-readable decomposition output for a learning system.
---

# Decompose Learning Goal

Use this skill to turn a learning goal into a reviewed, DAG-ready learning plan.
The output must be useful to a human learner and structured enough for import into
the Learning Directed Graph project.
After the final JSON is ready, persist it through the bundled script so the
graph is queryable by the project's existing CLI tables and indexes.

## Workflow

1. Capture the goal, known domains, audience level, constraints, and desired output use.
   If the prompt is broad, state narrow assumptions before decomposing.
2. Search only when the topic is time-sensitive, version-specific, standards-based,
   research-heavy, or when factual uncertainty would change the node structure.
3. Produce a first decomposition with:
   - atomic and non-atomic nodes
   - prerequisite edges
   - concept fingerprints
   - strictness levels
   - QA drafts for atomic nodes
4. Use available subagent capability for independent review when the host tool
   provides it:
   - Codex environment: use a Codex subagent.
   - Claude Code environment: use a Claude Code Task/subagent.
   - Other or no subagent support: skip independent review and mark it explicitly.
   Run review in two layers when subagents are available:
   - Local batch review: for each non-atomic parent that is decomposed into children,
     review that children batch as a unit.
   - Global DAG review: after the final graph is assembled, review the full graph for
     missing dependencies, cycles, duplication, and import readiness.
   Do not review every node in isolation; review the parent-to-children decomposition
   batch and then the whole DAG.
5. Revise the decomposition using reviewer feedback. If reviewer concerns cannot be
   resolved confidently, keep them in `unresolved_questions`.
6. Output both a concise human-readable plan and a machine-readable JSON block matching
   `references/output-schema.md`.
7. Persist the final JSON with `scripts/persist_result.py`.
   - If the user does not specify a DB location, use the current execution
     project root and write `learning.db` there.
   - If the user specifies a directory, write `learning.db` inside it.
   - If the user specifies a `.db`, `.sqlite`, or `.sqlite3` path, use that file.
   - The script initialises the current CLI schema from `src.data.database.SCHEMA_SQL`
     and appends skill import metadata tables and indexes.
   - If the user wants the existing CLI to read a non-default database, tell them
     to run the CLI with `DB_PATH=<db_path>` or persist with `--db data/learning.db`.
8. Report the DB path, `goal_id`, and `import_id` from the script summary.

## Review and Persistence Rules

- When setting review fields, choose a ReviewStatus enum member and choose a ReviewProvider enum member first; serialize the enum values into the final JSON.
  Do not invent ad hoc status or provider strings.
- If no subagent capability is available, still output the final human-readable plan
  and JSON, but set `review.status` to `ReviewStatus.SKIPPED` (`"skipped"` in JSON),
  set `review.provider` to `ReviewProvider.NONE` (`"none"` in JSON), and include an
  issue stating that no independent subagent review was performed. Local batch and
  global checks should also use skipped/none.
- Prefer concrete prerequisite edges over vague grouping edges.
- Treat an atomic node as valid only when it can be learned in about 15 minutes,
  has at least 3 discriminating QA checks, and contains no hidden sub-concepts.
- Use `critical` only for knowledge where misunderstanding creates high downstream
  risk; use `familiarity` only when recognition is enough.
- Do not generate SQL from model output. Always use `scripts/persist_result.py`
  for database creation, schema creation, indexing, validation, and inserts.
- The final JSON must include exactly one root node with `parent_title: null` so
  it can map to `learning_goals.root_node` and the CLI tree view.
- Use only `edge_type` values accepted by the output schema. `prerequisite` edges
  must connect atomic nodes and must not create cycles; use `parent_title` for
  decomposition hierarchy instead of parent-to-child prerequisite edges.
- Re-importing identical JSON is idempotent per `user_id`; different users get
  separate CLI-visible goals.
- If persistence is impossible because the current environment cannot write files
  or the user explicitly asks for output only, return the human-readable plan and
  JSON and state that persistence was skipped.

## References

- `references/output-schema.md`: Required final JSON structure.
- `references/review-checklist.md`: Provider-agnostic reviewer subagent checklist and expected response.
- `references/example-output.md`: Compact example of the expected final shape.
- `scripts/persist_result.py`: Deterministic JSON/Markdown-to-SQLite persistence step.
