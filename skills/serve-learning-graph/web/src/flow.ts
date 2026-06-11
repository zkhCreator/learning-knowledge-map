/*
File: skills/serve-learning-graph/web/src/flow.ts

Purpose:
    Convert Learning Directed Graph JSON into React Flow nodes and edges.

Responsibilities:
    - Compute deterministic left-to-right tree positions from parent-child hierarchy
    - Keep unattached trees visible in the same canvas
    - Style parent, prerequisite, and analogy edges differently

What this file does NOT do:
    - Fetch graph JSON
    - Render React components
    - Mutate backend graph data

Inputs: GraphPayload from /graph.json
Outputs: React Flow node/edge arrays plus an id-to-node lookup
*/

import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GraphEdge, GraphNode, GraphPayload } from "./types";

export interface LearningNodeData extends Record<string, unknown> {
  graphNode: GraphNode;
}

export type LearningFlowNode = Node<LearningNodeData, "learningNode">;
export type LearningFlowEdge = Edge;

export interface FlowGraph {
  nodes: LearningFlowNode[];
  edges: LearningFlowEdge[];
  nodeById: Map<string, GraphNode>;
}

// Left-to-right layout: tree depth advances along X, siblings stack along Y.
const DEPTH_SPACING = 320;
const SIBLING_SPACING = 150;
const FOREST_GAP = 1.4;

function graphTrees(payload: GraphPayload): GraphNode[] {
  return payload.trees?.length ? payload.trees : [payload.tree];
}

function collectNodes(node: GraphNode, result: GraphNode[] = []): GraphNode[] {
  result.push(node);
  for (const child of node.children ?? []) {
    collectNodes(child, result);
  }
  return result;
}

function measureLeaves(node: GraphNode): number {
  if (!node.children?.length) return 1;
  return node.children.reduce((sum, child) => sum + measureLeaves(child), 0);
}

function layoutTree(
  node: GraphNode,
  depth: number,
  leftLeaf: number,
  positions: Map<string, { x: number; y: number }>
): number {
  if (!node.children?.length) {
    positions.set(node.id, { x: depth * DEPTH_SPACING, y: leftLeaf * SIBLING_SPACING });
    return leftLeaf + 1;
  }

  let nextLeaf = leftLeaf;
  for (const child of node.children) {
    nextLeaf = layoutTree(child, depth + 1, nextLeaf, positions);
  }

  const childPositions = node.children
    .map((child) => positions.get(child.id))
    .filter((position): position is { x: number; y: number } => Boolean(position));
  const averageY =
    childPositions.reduce((sum, position) => sum + position.y, 0) /
    Math.max(childPositions.length, 1);
  positions.set(node.id, { x: depth * DEPTH_SPACING, y: averageY });
  return nextLeaf;
}

function buildPositions(trees: GraphNode[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  let leafOffset = 0;

  for (const tree of trees) {
    layoutTree(tree, 0, leafOffset, positions);
    leafOffset += measureLeaves(tree) + FOREST_GAP;
  }

  return positions;
}

function parentEdges(node: GraphNode, result: LearningFlowEdge[] = []): LearningFlowEdge[] {
  for (const child of node.children ?? []) {
    result.push({
      id: `parent:${node.id}:${child.id}`,
      source: node.id,
      target: child.id,
      type: "smoothstep",
      label: "parent",
      style: { stroke: "#2563eb", strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#2563eb" },
      data: { relation: "parent" },
    });
    parentEdges(child, result);
  }
  return result;
}

function relationEdge(edge: GraphEdge, visibleIds: Set<string>): LearningFlowEdge | null {
  if (!visibleIds.has(edge.from_node) || !visibleIds.has(edge.to_node)) {
    return null;
  }

  if (edge.edge_type === "prerequisite") {
    return {
      id: `prerequisite:${edge.id}`,
      source: edge.from_node,
      target: edge.to_node,
      type: "bezier",
      label: "prereq",
      style: { stroke: "#a16207", strokeWidth: 1.8, strokeDasharray: "7 5" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#a16207" },
      data: { relation: "prerequisite" },
    };
  }

  if (edge.edge_type === "cross_domain_analogy") {
    return {
      id: `analogy:${edge.id}`,
      source: edge.from_node,
      target: edge.to_node,
      type: "straight",
      label: "analogy",
      style: { stroke: "#0f766e", strokeWidth: 1.6, strokeDasharray: "2 6" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#0f766e" },
      data: { relation: "analogy" },
    };
  }

  return null;
}

export function buildFlowGraph(payload: GraphPayload): FlowGraph {
  const trees = graphTrees(payload);
  const treeNodes = trees.flatMap((tree) => collectNodes(tree));
  const nodeById = new Map(treeNodes.map((node) => [node.id, node]));
  const positions = buildPositions(trees);
  const visibleIds = new Set(nodeById.keys());

  const nodes: LearningFlowNode[] = treeNodes.map((node) => ({
    id: node.id,
    type: "learningNode",
    position: positions.get(node.id) ?? { x: 0, y: 0 },
    data: { graphNode: node },
  }));

  const hierarchyEdges = trees.flatMap((tree) => parentEdges(tree));
  const relationEdges = payload.edges
    .map((edge) => relationEdge(edge, visibleIds))
    .filter((edge): edge is LearningFlowEdge => Boolean(edge));

  return {
    nodes,
    edges: [...hierarchyEdges, ...relationEdges],
    nodeById,
  };
}
