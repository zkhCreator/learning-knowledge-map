/*
File: skills/serve-learning-graph/web/src/mastery.ts

Purpose:
    TypeScript mirror of the Ebbinghaus effective-mastery formula in
    src/domain/dag.py (effective_mastery, lines 33-56) so the graph view can
    show time-decayed mastery without a new API field.

Responsibilities:
    - effectiveMastery(raw, stability, lastReviewed): raw × e^(−days/stability)
      with the same edge-case behavior as the Python source of truth
    - Stay pinned to dag.py via the shared sample table in docs/24
      (mastery.test.ts asserts the same values as the Python tests)

What this file does NOT do:
    - Fetch data or read SQLite
    - Update stability or schedule reviews (Python-only concerns)

Inputs: KnowledgeState fields already present in the graph payload
Outputs: a 0..1 effective mastery number
*/

export function effectiveMastery(
  rawScore: number | null | undefined,
  stability: number | null | undefined,
  lastReviewed: string | null | undefined
): number {
  const raw = rawScore ?? 0;
  if (!lastReviewed || raw === 0) return 0;

  // dag.py treats naive timestamps as UTC; Date.parse would use local time for
  // a datetime without a timezone suffix, so normalise before parsing.
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(lastReviewed);
  const normalized =
    !hasTimezone && lastReviewed.includes("T") ? `${lastReviewed}Z` : lastReviewed;
  const reviewedAt = Date.parse(normalized);
  if (Number.isNaN(reviewedAt)) return raw; // unparseable → raw (matches dag.py)

  const daysElapsed = (Date.now() - reviewedAt) / 86_400_000;
  const safeStability = Math.max(stability ?? 1.0, 0.1);
  return raw * Math.exp(-daysElapsed / safeStability);
}
