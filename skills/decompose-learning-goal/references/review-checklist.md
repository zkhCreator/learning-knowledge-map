# Review Checklist

Use this checklist with whichever reviewer subagent the current host tool provides:
Codex subagent, Claude Code Task/subagent, or another equivalent independent agent.

Review runs in two layers:

- Local batch review: review one parent-to-children decomposition batch as a unit.
  This checks whether the children collectively cover the parent, have balanced
  granularity, and have correct sibling dependencies.
- Global DAG review: review the fully assembled graph for missing dependencies,
  duplicates, cycles, and import readiness.

Do not review each node in isolation. Single-node review loses the context needed
to judge omissions and prerequisite structure.

Give the reviewer subagent only the relevant raw decomposition, the original goal,
known domains, assumptions, and sources. Do not include the planner's self-evaluation
or expected answer.

Ask the reviewer to return:

```json
{
  "status": "approved",
  "provider": "codex",
  "issues": ["string"],
  "suggestions": ["string"]
}
```

## Required Checks

- Completeness: Does the decomposition cover the target outcome?
- Granularity: Are atomic nodes small enough for about 15 minutes of focused learning?
- Atomicity: Does each atomic node have at least 3 useful QA checks?
- Dependency correctness: Are prerequisite edges directionally correct?
- Cycle risk: Would any edges create a circular learning dependency?
- Missing cross-domain prerequisites: Are assumed foundations stated as nodes?
- Strictness: Are `critical`, `standard`, and `familiarity` labels justified?
- Source use: Are searched facts reflected accurately and not overgeneralized?
- Import readiness: Can titles, nodes, edges, and review data fit the output schema
  and `scripts/persist_result.py` validation?
- Root mapping: Is there exactly one root node with `parent_title: null` for
  `learning_goals.root_node`?
- Title references: Does every `parent_title`, `from_title`, and `to_title` match
  an existing node title exactly?
- Edge type: Is every edge type one of `prerequisite` or `cross_domain_analogy`?
- Atomic learning order: Do all `prerequisite` edges connect atomic nodes and
  avoid cycles?

## Layer-Specific Checks

- Local batch review: focus on one `parent_title` and its proposed children.
- Global DAG review: focus on the whole graph after local fixes are applied.

## Reviewer Behavior

- Be skeptical but specific.
- Prefer concrete replacement suggestions over broad criticism.
- Mark `status` with the `ReviewStatus` enum value `"approved"` only when no
  structural issue remains.
- Mark `status` with the `ReviewStatus` enum value `"rejected"` when issues remain
  that would change nodes, edges, source use, or import readiness.
- Mark `provider` with the `ReviewProvider` enum value for the active reviewer
  host: `"codex"` or `"claude"`.
- If the goal is too broad, request narrowing instead of approving a shallow graph.
