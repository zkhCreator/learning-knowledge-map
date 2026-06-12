/*
File: skills/serve-learning-graph/web/src/features/graph/GraphView.tsx

Purpose:
    Roadmap-style React Flow view for a Learning Directed Graph (docs/24).
    The learning main line (root + trunk steps) reads top-to-bottom; subtrees
    hang off the trunk as collapsible side branches; node color and a progress
    ring show mastery at a glance.

Responsibilities:
    - Fetch graph JSON from the local Node server (/graph.json)
    - Render the roadmap layout with per-trunk-step collapse/expand
    - Show selected-node details (status, effective mastery, relations) plus
      plain ?view=… deep links into the Learn / Exam / Review flows
    - Surface the graph payload to the shell via onPayload for the summary strip

What this file does NOT do:
    - Start the server
    - Read SQLite directly
    - Modify graph state or persist UI edits
    - Drive tab/route state internally — flow entries are ordinary URL links,
      navigation stays with the browser

Inputs: /graph.json from server.js
Outputs: Roadmap graph UI + selected-node detail panel
*/

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type OnNodeDrag,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  buildRoadmapGraph,
  collapsibleTrunkIds,
  type LearningFlowEdge,
  type LearningFlowNode,
} from "../../flow";
import { effectiveMastery } from "../../mastery";
import type { GraphNode, GraphPayload, RelatedNode } from "../../types";

function statusLabel(node: GraphNode): string {
  return node.state?.status ?? "unknown";
}

function nodeMastery(node: GraphNode): number {
  return effectiveMastery(
    node.state?.raw_score,
    node.state?.stability,
    node.state?.last_reviewed
  );
}

function shortId(id: string): string {
  return id.length <= 8 ? id : id.slice(0, 8);
}

const RING_RADIUS = 13;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

function ProgressRing({ value, status }: { value: number; status: string }) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <span className={`progress-ring ring-${status}`} title={`${Math.round(clamped * 100)}%`}>
      <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden>
        <circle className="ring-track" cx="16" cy="16" r={RING_RADIUS} />
        <circle
          className="ring-value"
          cx="16"
          cy="16"
          r={RING_RADIUS}
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={RING_CIRCUMFERENCE * (1 - clamped)}
        />
      </svg>
      <span className="ring-label">
        {status === "mastered" ? "✓" : `${Math.round(clamped * 100)}`}
      </span>
    </span>
  );
}

function LearningNode({ data, selected }: NodeProps<LearningFlowNode>) {
  const node = data.graphNode;
  const status = statusLabel(node);
  const kind = node.is_atomic ? "atomic" : "group";

  return (
    <div
      className={`learning-node roadmap status-${status} ${kind} ${selected ? "selected" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="node-handle" />
      <Handle type="target" position={Position.Top} id="t-top" className="node-handle" />
      <Handle type="target" position={Position.Right} id="t-right" className="node-handle" />
      <div className="node-row">
        <ProgressRing value={nodeMastery(node)} status={status} />
        <div className="node-main">
          <div className="node-title">{node.title}</div>
          <div className="node-meta">
            <span>{node.domain || "general"}</span>
            {node.est_minutes != null ? <span>{node.est_minutes} min</span> : null}
          </div>
        </div>
        {data.collapsible ? (
          <button
            type="button"
            className="collapse-toggle"
            aria-label={data.collapsed ? "Expand branch" : "Collapse branch"}
            onClick={(event) => {
              event.stopPropagation();
              data.onToggle?.(node.id);
            }}
          >
            {data.collapsed ? "▸" : "▾"}
          </button>
        ) : null}
      </div>
      <div className="node-badges">
        <span className={`badge ${kind}`}>{kind}</span>
        <span className={`badge status-${status}`}>{status}</span>
        {data.collapsible && data.collapsed ? (
          <span className="badge subtree-count">+{data.descendantCount} topics</span>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} className="node-handle" />
      <Handle type="source" position={Position.Bottom} id="s-bottom" className="node-handle" />
      <Handle type="source" position={Position.Left} id="s-left" className="node-handle" />
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

  const status = statusLabel(node);
  const mastery = Math.round(nodeMastery(node) * 100);

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <span className={`badge ${node.is_atomic ? "atomic" : "group"}`}>
          {node.is_atomic ? "atomic" : "group"}
        </span>
        <span className={`badge status-${status}`}>{status}</span>
        <span className="badge mastery">{mastery}% mastery</span>
      </div>
      <h2>{node.title}</h2>
      <p className="description">{node.description || "No description."}</p>

      {node.is_atomic ? (
        <div className="detail-actions">
          <a className="action-link" href={`?view=learn&node=${node.id}`}>
            📖 学习
          </a>
          <a className="action-link" href={`?view=exam&node=${node.id}`}>
            📝 考试
          </a>
          <a className="action-link" href={`?view=review&node=${node.id}`}>
            🔄 复习
          </a>
        </div>
      ) : null}

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
  // Collapsed trunk steps; null until the payload arrives (default: all collapsed
  // so the first screen is just the learning main line, docs/24).
  const [collapsed, setCollapsed] = useState<Set<string> | null>(null);

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
        setCollapsed(new Set(collapsibleTrunkIds(data)));
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

  const toggleCollapse = useCallback((id: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous ?? []);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const flowGraph = useMemo(
    () => (payload && collapsed ? buildRoadmapGraph(payload, collapsed) : null),
    [payload, collapsed]
  );
  const selectedNode = selectedId && flowGraph ? flowGraph.nodeById.get(selectedId) ?? null : null;

  // Local node/edge state lets users drag nodes. Collapse/expand recomputes
  // the layout, but nodes the user dragged keep their hand-placed positions.
  const [nodes, setNodes, onNodesChange] = useNodesState<LearningFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<LearningFlowEdge>([]);
  const draggedIds = useRef(new Set<string>());

  useEffect(() => {
    if (!flowGraph) return;
    setNodes((previous) => {
      const keptPositions = new Map(
        previous
          .filter((node) => draggedIds.current.has(node.id))
          .map((node) => [node.id, node.position])
      );
      return flowGraph.nodes.map((node) => ({
        ...node,
        position: keptPositions.get(node.id) ?? node.position,
        data: { ...node.data, onToggle: toggleCollapse },
      }));
    });
    setEdges(flowGraph.edges);
  }, [flowGraph, setNodes, setEdges, toggleCollapse]);

  const onNodeDragStop = useCallback<OnNodeDrag<LearningFlowNode>>((_, node) => {
    draggedIds.current.add(node.id);
  }, []);

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
          onNodeDragStop={onNodeDragStop}
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
