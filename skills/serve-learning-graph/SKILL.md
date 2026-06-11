---
name: serve-learning-graph
description: Start a foreground Node server that renders an existing Learning Directed Graph SQLite database as a local React Flow knowledge graph page. Use when a user asks to open, view, browse, serve, or render DB graph content in a web page.
---

# Serve Learning Graph

Use this skill to open an existing Learning Directed Graph SQLite database in a
local React Flow page. This is the visual Node-server rendering skill.

## Workflow

1. Resolve the database path:
   - Use the user's explicit DB path when provided.
   - Otherwise use `DB_PATH` from the environment when set.
   - Otherwise use `data/learning.db` under the current project root.
2. Resolve the goal:
   - Use a full goal ID or prefix when provided.
   - If no goal is provided and the user has exactly one goal, use it.
   - If there are no goals or multiple possible goals, ask for a goal ID/prefix.
3. Start the foreground Node server:

   ```bash
   node skills/serve-learning-graph/scripts/server.js --db data/learning.db --goal <goal-id-or-prefix> --user default --port 8765
   ```

4. Tell the user the URL printed by the server. The server stops with `Ctrl+C`.

If the React build is missing after frontend edits, rebuild it first:

```bash
cd skills/serve-learning-graph/web
pnpm install
pnpm build
```

## Rendering Rules

- Use `knowledge_nodes.parent_node` as the React Flow tree hierarchy.
- Render parent-child, prerequisite, and cross-domain analogy edges with distinct styles.
- Show `knowledge_edges` details in the selected-node panel.
- Show both intermediate and atomic nodes.
- Start from port `8765` unless the user specifies another port.
- If the preferred port is occupied, automatically try the next port.
- Do not write to the database.
- Do not install npm packages at runtime; the server only serves the committed Vite build.

## Scripts

- `scripts/export_graph.py` reads SQLite and emits graph JSON.
- `scripts/server.js` starts the HTTP server and serves `web/dist`.
- `web/` contains the React + TypeScript + React Flow source.
- The server supports `--no-open` for automated tests.
