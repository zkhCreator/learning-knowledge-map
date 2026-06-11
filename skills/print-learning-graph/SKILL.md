---
name: print-learning-graph
description: Print an existing Learning Directed Graph SQLite database as a parent-child knowledge tree. Use when a user asks to inspect, render, show, print, or summarize DB graph content in the terminal without starting a web server.
---

# Print Learning Graph

Use this skill to inspect an existing Learning Directed Graph SQLite database
as a terminal tree. This is the basic text-rendering skill.

## Workflow

1. Resolve the database path:
   - Use the user's explicit DB path when provided.
   - Otherwise use `DB_PATH` from the environment when set.
   - Otherwise use `data/learning.db` under the current project root.
2. Resolve the goal:
   - Use a full goal ID or prefix when provided.
   - If no goal is provided and the user has exactly one goal, use it.
   - If there are no goals or multiple possible goals, ask for a goal ID/prefix.
3. Run the bundled script:

   ```bash
   python3 skills/print-learning-graph/scripts/print_graph.py --db data/learning.db --goal <goal-id-or-prefix> --user default
   ```

4. Return the printed tree to the user. Mention the DB path and goal ID if the
   user may need to run follow-up commands.

## Rendering Rules

- Use `knowledge_nodes.parent_node` as the tree hierarchy.
- Treat `knowledge_edges` as dependency metadata, not as parent-child links.
- Show both intermediate and atomic nodes.
- Do not write to the database.
- Do not call LLMs or regenerate graph content.

## Script

- `scripts/print_graph.py` is self-contained and only uses Python standard
  library modules.
