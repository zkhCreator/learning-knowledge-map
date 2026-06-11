/*
File: skills/serve-learning-graph/web/src/features/graph/GraphView.tsx

Purpose:
    React Flow view for inspecting a Learning Directed Graph. This is the
    graph feature of the GUI workflow host; it preserves the read-only
    behavior of the original graph-only app.

Responsibilities:
    - Fetch graph JSON from the local Node server (/graph.json)
    - Render nodes and typed edges with React Flow
    - Show selected-node details without writing to the database
    - Surface the graph payload to the shell via onPayload for the summary strip

What this file does NOT do:
    - Start the server
    - Read SQLite directly
    - Modify graph state or persist UI edits
    - Drive tab/route state or jump into other flows

Inputs: /graph.json from server.js
Outputs: React Flow graph UI + selected-node detail panel
*/

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  Handle,
  useEdgesState,
  useNodesState,
  type NodeProps,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { buildFlowGraph, type LearningFlowEdge, type LearningFlowNode } from "../../flow";
import type { GraphNode, GraphPayload, RelatedNode } from "../../types";

function statusLabel(node: GraphNode): string {
  return node.state?.status ?? "unknown";
}

function shortId(id: string): string {
  return id.length <= 8 ? id : id.slice(0, 8);
}

function LearningNode({ data, selected }: NodeProps<LearningFlowNode>) {
  const node = data.graphNode;
  const status = statusLabel(node);
  const kind = node.is_atomic ? "atomic" : "group";

  return (
    <div className={`learning-node ${kind} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} className="node-handle" />
      <div className="node-title">{node.title}</div>
      <div className="node-meta">
        <span>{node.domain || "general"}</span>
        {node.est_minutes != null ? <span>{node.est_minutes} min</span> : null}
      </div>
      <div className="node-badges">
        <span className={`badge ${kind}`}>{kind}</span>
        <span className={`badge status-${status}`}>{status}</span>
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
    </div>
  );
}

const nodeTypes = {
  learningNode: LearningNode,
};

function RelatedList({
  title,
  items,
}: {
  title: string;
  items: RelatedNode[];
}) {
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      {items.length ? (
        <ul className="related-list">
          {items.map((item) => (
            <li key={`${title}:${item.id}:${item.node_id}`}>
              <span>{item.title}</span>
              {item.analogy_desc ? <small>{item.analogy_desc}</small> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty">None</p>
      )}
    </section>
  );
}

function DetailPanel({ node }: { node: GraphNode | null }) {
  if (!node) {
    return (
      <aside className="detail-panel">
        <p className="empty">Select a node</p>
      </aside>
    );
  }

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <span className={`badge ${node.is_atomic ? "atomic" : "group"}`}>
          {node.is_atomic ? "atomic" : "group"}
        </span>
        <span className={`badge status-${statusLabel(node)}`}>{statusLabel(node)}</span>
      </div>
      <h2>{node.title}</h2>
      <p className="description">{node.description || "No description."}</p>

      <dl className="facts">
        <dt>ID</dt>
        <dd>{shortId(node.id)}</dd>
        <dt>Domain</dt>
        <dd>{node.domain || "-"}</dd>
        <dt>Difficulty</dt>
        <dd>{node.difficulty ?? "-"}</dd>
        <dt>Minutes</dt>
        <dd>{node.est_minutes ?? "-"}</dd>
        <dt>Strictness</dt>
        <dd>{node.strictness_level || "-"}</dd>
        <dt>Threshold</dt>
        <dd>{node.mastery_threshold ?? "-"}</dd>
      </dl>

      <RelatedList title="Prerequisites" items={node.prerequisites} />
      <RelatedList title="Analogies" items={node.analogies} />
      <RelatedList title="Dependents" items={node.dependents} />
    </aside>
  );
}

export interface GraphViewProps {
  /** Notify the shell of the loaded graph payload (for the summary strip). */
  onPayload?: (payload: GraphPayload | null) => void;
}

function GraphContent({ onPayload }: GraphViewProps) {
  const [payload, setPayload] = useState<GraphPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/graph.json")
      .then((response) => {
        if (!response.ok) {
          throw new Error(`graph.json returned ${response.status}`);
        }
        return response.json() as Promise<GraphPayload>;
      })
      .then((data) => {
        if (cancelled) return;
        setPayload(data);
        setSelectedId((data.trees?.[0] ?? data.tree).id);
        onPayload?.(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
    };
  }, [onPayload]);

  const flowGraph = useMemo(() => (payload ? buildFlowGraph(payload) : null), [payload]);
  const selectedNode = selectedId && flowGraph ? flowGraph.nodeById.get(selectedId) ?? null : null;

  // Local node/edge state lets users drag nodes and keeps the moved positions.
  // Re-seed it whenever a freshly computed layout arrives.
  const [nodes, setNodes, onNodesChange] = useNodesState<LearningFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<LearningFlowEdge>([]);

  useEffect(() => {
    if (!flowGraph) return;
    setNodes(flowGraph.nodes);
    setEdges(flowGraph.edges);
  }, [flowGraph, setNodes, setEdges]);

  const onNodeClick = useCallback<NodeMouseHandler<LearningFlowNode>>((_, node) => {
    setSelectedId(node.id);
  }, []);

  if (error) {
    return (
      <main className="state-screen">
        <h1>Learning Graph</h1>
        <p>{error}</p>
      </main>
    );
  }

  if (!payload || !flowGraph) {
    return (
      <main className="state-screen">
        <h1>Learning Graph</h1>
        <p>Loading graph...</p>
      </main>
    );
  }

  return (
    <div className="graph-view">
      <section className="flow-surface" aria-label="Learning graph">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          nodesDraggable
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.12}
        >
          <Background gap={18} color="#d8dde8" />
          <MiniMap pannable zoomable nodeStrokeWidth={3} />
          <Controls />
        </ReactFlow>
      </section>
      <DetailPanel node={selectedNode} />
    </div>
  );
}

export default function GraphView(props: GraphViewProps) {
  return (
    <ReactFlowProvider>
      <GraphContent {...props} />
    </ReactFlowProvider>
  );
}
