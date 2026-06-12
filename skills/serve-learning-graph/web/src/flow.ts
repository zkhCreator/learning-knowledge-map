/*
File: skills/serve-learning-graph/web/src/flow.ts

Purpose:
    Convert Learning Directed Graph JSON into React Flow nodes and edges.

Responsibilities:
    - Compute deterministic left-to-right tree positions from parent-child hierarchy
      (classic view, buildFlowGraph)
    - Compute the roadmap layout (docs/24): trunk = root + direct children in a
      centered vertical column, subtrees fanned out as alternating left/right
      side branches, with per-trunk-step collapse via hidden:true
      (buildRoadmapGraph)
    - Keep unattached trees visible in the same canvas
    - Style parent, prerequisite, and analogy edges differently

What this file does NOT do:
    - Fetch graph JSON
    - Render React components
    - Own collapse state (the view passes the collapsed set in)
    - Mutate backend graph data

Inputs: GraphPayload from /graph.json (+ collapsed trunk-step ids for roadmap)
Outputs: React Flow node/edge arrays plus an id-to-node lookup
*/

import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GraphEdge, GraphNode, GraphPayload } from "./types";

export interface LearningNodeData extends Record<string, unknown> {
  graphNode: GraphNode;
  /** Roadmap view only: this trunk step can be collapsed/expanded. */
  collapsible?: boolean;
  /** Roadmap view only: current collapse state of a collapsible trunk step. */
  collapsed?: boolean;
  /** Roadmap view only: descendant count shown on a collapsed trunk step. */
  descendantCount?: number;
  /** Injected by the view: toggle collapse for this trunk step. */
  onToggle?: (id: string) => void;
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

/* ── Roadmap layout (docs/24) ─────────────────────────────────────────────── */

// Trunk steps stack vertically; side branches fan out horizontally.
const TRUNK_SPACING = 170;
const BRANCH_DEPTH_SPACING = 300;
const BRANCH_SIBLING_SPACING = 130;
const BRANCH_OFFSET = 360;

/** Trunk steps (root's direct children) that have a subtree to collapse. */
export function collapsibleTrunkIds(payload: GraphPayload): string[] {
  return graphTrees(payload).flatMap((tree) =>
    (tree.children ?? [])
      .filter((step) => (step.children ?? []).length > 0)
      .map((step) => step.id)
  );
}

function layoutBranch(
  node: GraphNode,
  depth: number,
  leftLeaf: number,
  side: 1 | -1,
  origin: { x: number; y: number },
  positions: Map<string, { x: number; y: number }>
): number {
  if (!node.children?.length) {
    positions.set(node.id, {
      x: origin.x + side * (BRANCH_OFFSET + (depth - 1) * BRANCH_DEPTH_SPACING),
      y: origin.y + leftLeaf * BRANCH_SIBLING_SPACING,
    });
    return leftLeaf + 1;
  }
  let nextLeaf = leftLeaf;
  for (const child of node.children) {
    nextLeaf = layoutBranch(child, depth + 1, nextLeaf, side, origin, positions);
  }
  const childPositions = node.children
    .map((child) => positions.get(child.id))
    .filter((position): position is { x: number; y: number } => Boolean(position));
  const averageY =
    childPositions.reduce((sum, position) => sum + position.y, 0) /
    Math.max(childPositions.length, 1);
  positions.set(node.id, {
    x: origin.x + side * (BRANCH_OFFSET + (depth - 1) * BRANCH_DEPTH_SPACING),
    y: averageY,
  });
  return nextLeaf;
}

function countDescendants(node: GraphNode): number {
  return (node.children ?? []).reduce(
    (sum, child) => sum + 1 + countDescendants(child),
    0
  );
}

function collectHidden(node: GraphNode, hidden: Set<string>): void {
  for (const child of node.children ?? []) {
    hidden.add(child.id);
    collectHidden(child, hidden);
  }
}

export function buildRoadmapGraph(
  payload: GraphPayload,
  collapsed: Set<string>
): FlowGraph {
  const trees = graphTrees(payload);
  const treeNodes = trees.flatMap((tree) => collectNodes(tree));
  const nodeById = new Map(treeNodes.map((node) => [node.id, node]));

  const positions = new Map<string, { x: number; y: number }>();
  const hiddenIds = new Set<string>();
  let cursorY = 0;

  for (const tree of trees) {
    positions.set(tree.id, { x: 0, y: cursorY });
    cursorY += TRUNK_SPACING;

    (tree.children ?? []).forEach((step, index) => {
      const isCollapsed = collapsed.has(step.id);
      const side: 1 | -1 = index % 2 === 0 ? 1 : -1;
      const branchLeaves =
        !isCollapsed && step.children?.length ? measureLeaves(step) : 1;
      const bandHeight = branchLeaves * BRANCH_SIBLING_SPACING;
      const stepY = cursorY + (bandHeight - BRANCH_SIBLING_SPACING) / 2;
      positions.set(step.id, { x: 0, y: stepY });

      if (isCollapsed) {
        collectHidden(step, hiddenIds);
      } else if (step.children?.length) {
        let nextLeaf = 0;
        const origin = { x: 0, y: cursorY };
        for (const child of step.children) {
          nextLeaf = layoutBranch(child, 1, nextLeaf, side, origin, positions);
        }
      }
      cursorY += bandHeight + TRUNK_SPACING / 2;
    });
    cursorY += TRUNK_SPACING * FOREST_GAP;
  }

  const trunkSteps = new Set(
    trees.flatMap((tree) => (tree.children ?? []).map((step) => step.id))
  );

  const nodes: LearningFlowNode[] = treeNodes.map((node) => ({
    id: node.id,
    type: "learningNode",
    position: positions.get(node.id) ?? { x: 0, y: 0 },
    hidden: hiddenIds.has(node.id),
    data: {
      graphNode: node,
      collapsible: trunkSteps.has(node.id) && (node.children ?? []).length > 0,
      collapsed: collapsed.has(node.id),
      descendantCount: countDescendants(node),
    },
  }));

  const visibleIds = new Set(
    treeNodes.map((node) => node.id).filter((id) => !hiddenIds.has(id))
  );
  // Route trunk edges top→bottom and left-side branch edges right→left; the
  // default (no handle id) pair is the classic left→right routing.
  const positionOf = (id: string) => positions.get(id) ?? { x: 0, y: 0 };
  const hierarchyEdges = trees
    .flatMap((tree) => parentEdges(tree))
    .map((edge) => {
      const source = positionOf(edge.source);
      const target = positionOf(edge.target);
      let sourceHandle: string | undefined;
      let targetHandle: string | undefined;
      if (target.x === source.x) {
        sourceHandle = "s-bottom";
        targetHandle = "t-top";
      } else if (target.x < source.x) {
        sourceHandle = "s-left";
        targetHandle = "t-right";
      }
      return {
        ...edge,
        sourceHandle,
        targetHandle,
        hidden: !visibleIds.has(edge.source) || !visibleIds.has(edge.target),
      };
    });
  const relationEdges = payload.edges
    .map((edge) => relationEdge(edge, visibleIds))
    .filter((edge): edge is LearningFlowEdge => Boolean(edge));

  return {
    nodes,
    edges: [...hierarchyEdges, ...relationEdges],
    nodeById,
  };
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
