/*
File: skills/serve-learning-graph/web/src/components/SummaryStrip.tsx

Purpose:
    Goal/summary header strip for the GUI workflow host shell.

Responsibilities:
    - Show the active goal title and database path
    - Show graph summary counts (nodes, atomic, edges, est minutes)
    - Reuse the content of the original graph-only topbar so the shell keeps
      the same at-a-glance context across every flow

What this file does NOT do:
    - Fetch data (the graph view loads the payload and hands it up)
    - Hold tab/route state
    - Implement workflow feature logic

Inputs: optional GraphPayload (null until the graph view loads it)
Outputs: A summary header strip
*/

import type { GraphPayload } from "../types";

export interface SummaryStripProps {
  payload: GraphPayload | null;
}

export default function SummaryStrip({ payload }: SummaryStripProps) {
  if (!payload) {
    return (
      <header className="host-summary">
        <div>
          <h1>Learning Workflow Host</h1>
          <p>Select a flow to begin.</p>
        </div>
      </header>
    );
  }

  return (
    <header className="host-summary">
      <div>
        <h1>{payload.goal.title}</h1>
        <p>{payload.db_path}</p>
      </div>
      <div className="summary">
        <span>{payload.summary.node_count} nodes</span>
        <span>{payload.summary.atomic_node_count} atomic</span>
        <span>{payload.summary.edge_count} edges</span>
        <span>{payload.summary.total_est_minutes} min</span>
      </div>
    </header>
  );
}
