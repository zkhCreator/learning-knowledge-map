/*
File: skills/serve-learning-graph/web/src/flow.test.ts

Purpose:
    Pure-function tests for the roadmap layout (docs/24): deterministic
    positions, trunk centering, collapse hiding, and vertical tightening.
*/

import { describe, expect, it } from "vitest";
import { buildRoadmapGraph, collapsibleTrunkIds } from "./flow";
import type { GraphNode, GraphPayload } from "./types";

function node(id: string, children: GraphNode[] = []): GraphNode {
  return {
    id,
    title: id,
    description: "",
    domain: "test",
    concept_fingerprint: [],
    difficulty: null,
    est_minutes: null,
    qa_set: [],
    depth_level: 0,
    parent_node: null,
    strictness_level: "standard",
    mastery_threshold: 0.8,
    risk_note: "",
    is_atomic: children.length === 0,
    created_at: null,
    state: null,
    prerequisites: [],
    dependents: [],
    analogies: [],
    children,
  };
}

function payload(tree: GraphNode): GraphPayload {
  return {
    db_path: "/tmp/test.db",
    user_id: "default",
    goal: {
      id: "g1", user_id: "default", title: "Goal",
      root_node: tree.id, status: "active", created_at: "",
    },
    summary: { node_count: 0, atomic_node_count: 0, edge_count: 0, total_est_minutes: 0 },
    tree,
    trees: [tree],
    unattached_trees: [],
    nodes: [],
    edges: [],
  };
}

function sampleTree(): GraphNode {
  // root → step1 (2 leaves), step2 (leaf), step3 (1 leaf)
  return node("root", [
    node("step1", [node("s1a"), node("s1b")]),
    node("step2"),
    node("step3", [node("s3a")]),
  ]);
}

describe("collapsibleTrunkIds", () => {
  it("returns only trunk steps that have children", () => {
    expect(collapsibleTrunkIds(payload(sampleTree()))).toEqual(["step1", "step3"]);
  });
});

describe("buildRoadmapGraph", () => {
  it("is deterministic for identical input", () => {
    const a = buildRoadmapGraph(payload(sampleTree()), new Set(["step1"]));
    const b = buildRoadmapGraph(payload(sampleTree()), new Set(["step1"]));
    expect(a.nodes.map((n) => ({ id: n.id, ...n.position }))).toEqual(
      b.nodes.map((n) => ({ id: n.id, ...n.position }))
    );
  });

  it("keeps the trunk on the center column", () => {
    const graph = buildRoadmapGraph(payload(sampleTree()), new Set());
    for (const id of ["root", "step1", "step2", "step3"]) {
      const flowNode = graph.nodes.find((n) => n.id === id);
      expect(flowNode?.position.x).toBe(0);
    }
    // Trunk reads top-to-bottom.
    const ys = ["root", "step1", "step2", "step3"].map(
      (id) => graph.nodes.find((n) => n.id === id)!.position.y
    );
    expect([...ys].sort((a, b) => a - b)).toEqual(ys);
  });

  it("fans side branches out left/right alternately", () => {
    const tree = node("root", [
      node("step1", [node("s1a")]),
      node("step2", [node("s2a")]),
    ]);
    const graph = buildRoadmapGraph(payload(tree), new Set());
    const x = (id: string) => graph.nodes.find((n) => n.id === id)!.position.x;
    expect(x("s1a")).toBeGreaterThan(0); // step index 0 → right
    expect(x("s2a")).toBeLessThan(0); // step index 1 → left
  });

  it("hides collapsed subtrees (nodes and their edges)", () => {
    const graph = buildRoadmapGraph(payload(sampleTree()), new Set(["step1"]));
    const hidden = new Set(graph.nodes.filter((n) => n.hidden).map((n) => n.id));
    expect(hidden).toEqual(new Set(["s1a", "s1b"]));

    const edgeTo = (target: string) => graph.edges.find((e) => e.target === target);
    expect(edgeTo("s1a")?.hidden).toBe(true);
    expect(edgeTo("step1")?.hidden).toBe(false);
    expect(edgeTo("s3a")?.hidden).toBe(false);
  });

  it("collapsing tightens the trunk vertically", () => {
    const expanded = buildRoadmapGraph(payload(sampleTree()), new Set());
    const collapsedGraph = buildRoadmapGraph(
      payload(sampleTree()),
      new Set(["step1", "step3"])
    );
    const lastY = (graph: typeof expanded) =>
      graph.nodes.find((n) => n.id === "step3")!.position.y;
    expect(lastY(collapsedGraph)).toBeLessThan(lastY(expanded));
  });

  it("marks trunk steps with subtree as collapsible and counts descendants", () => {
    const graph = buildRoadmapGraph(payload(sampleTree()), new Set(["step1"]));
    const step1 = graph.nodes.find((n) => n.id === "step1")!;
    expect(step1.data.collapsible).toBe(true);
    expect(step1.data.collapsed).toBe(true);
    expect(step1.data.descendantCount).toBe(2);
    const step2 = graph.nodes.find((n) => n.id === "step2")!;
    expect(step2.data.collapsible).toBe(false);
  });
});
