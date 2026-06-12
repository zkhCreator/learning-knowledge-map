/*
File: skills/serve-learning-graph/web/src/mastery.test.ts

Purpose:
    Pin mastery.ts to the Python source of truth (src/domain/dag.py) using the
    shared sample table from docs/24. If either side changes formula or edge
    cases, one of the two suites goes red.
*/

import { describe, expect, it } from "vitest";
import { effectiveMastery } from "./mastery";

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

describe("effectiveMastery (docs/24 sample table)", () => {
  it("raw 0.9, stability 2.0, 1 day → ≈ 0.54588", () => {
    expect(effectiveMastery(0.9, 2.0, daysAgo(1))).toBeCloseTo(0.9 * Math.exp(-0.5), 4);
  });

  it("raw 0.9, stability 2.0, just reviewed → ≈ 0.9", () => {
    expect(effectiveMastery(0.9, 2.0, daysAgo(0))).toBeCloseTo(0.9, 3);
  });

  it("stability floors at 0.1 (raw 0.8, stability 0.05, 1 day → ≈ 3.63e-5)", () => {
    expect(effectiveMastery(0.8, 0.05, daysAgo(1))).toBeCloseTo(0.8 * Math.exp(-10), 8);
  });

  it("never reviewed → 0", () => {
    expect(effectiveMastery(0.9, 2.0, null)).toBe(0);
  });

  it("raw 0 → 0", () => {
    expect(effectiveMastery(0, 2.0, daysAgo(1))).toBe(0);
  });

  it("unparseable timestamp → raw score (matches dag.py fallback)", () => {
    expect(effectiveMastery(0.7, 2.0, "not-a-date")).toBe(0.7);
  });
});
