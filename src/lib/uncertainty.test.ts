import { describe, expect, it } from "vitest";
import {
  countConfidenceRadius,
  groupSuppressionThreshold,
  meanConfidenceRadius,
  meanIsUninformative,
} from "./uncertainty";

describe("uncertainty estimates", () => {
  it("matches the worker's count radius", () => {
    // dp_core.mechanisms.laplace.confidence_radius(1.0) == 2.9957
    expect(countConfidenceRadius(1)).toBeCloseTo(2.9957, 4);
    expect(countConfidenceRadius(0.5)).toBeCloseTo(5.9915, 4);
  });

  it("widens as epsilon falls", () => {
    expect(countConfidenceRadius(0.2)).toBeGreaterThan(countConfidenceRadius(1));
  });

  it("matches the worker's bounded-mean radius", () => {
    // The Python executor returns range 14.98 for bounds [0, 25], epsilon 1.0
    // and a denominator floored at 10 (see test_executor.py).
    expect(meanConfidenceRadius({ spread: 25, epsilon: 1, rows: 3, minDenominator: 10 }))
      .toBeCloseTo(14.98, 2);
    // And 17.97 for bounds [0, 30] under the same conditions.
    expect(meanConfidenceRadius({ spread: 30, epsilon: 1, rows: 3, minDenominator: 10 }))
      .toBeCloseTo(17.97, 2);
  });

  it("is twice the naive full-epsilon estimate", () => {
    // The pre-fix composer used spread / rows * ln(20) / epsilon, ignoring the
    // half-epsilon split. The corrected figure must be exactly double it.
    const spread = 72, epsilon = 0.4, rows = 500;
    const naive = (spread / rows) * Math.log(20) / epsilon;
    const corrected = meanConfidenceRadius({ spread, epsilon, rows, minDenominator: 10 });
    expect(corrected).toBeCloseTo(naive * 2, 10);
  });

  it("floors the denominator at the public minimum", () => {
    const tiny = meanConfidenceRadius({ spread: 100, epsilon: 1, rows: 2, minDenominator: 10 });
    const atFloor = meanConfidenceRadius({ spread: 100, epsilon: 1, rows: 10, minDenominator: 10 });
    expect(tiny).toBe(atFloor);
  });

  it("shrinks as the population grows", () => {
    const small = meanConfidenceRadius({ spread: 100, epsilon: 1, rows: 50, minDenominator: 10 });
    const large = meanConfidenceRadius({ spread: 100, epsilon: 1, rows: 5000, minDenominator: 10 });
    expect(large).toBeLessThan(small);
  });

  it("matches the worker's suppression threshold", () => {
    // executor.py: min_group_size + confidence_radius(epsilon / 2) == 5 + 5.99
    expect(groupSuppressionThreshold(5, 1)).toBeCloseTo(10.99, 2);
    expect(groupSuppressionThreshold(4, 1)).toBeCloseTo(9.99, 2);
  });

  it("always exceeds the nominal policy minimum", () => {
    for (const epsilon of [0.1, 0.5, 1, 2]) {
      expect(groupSuppressionThreshold(5, epsilon)).toBeGreaterThan(5);
    }
  });

  it("flags a mean whose noise swamps the bound range", () => {
    expect(meanIsUninformative(14.98, 25)).toBe(true);
    expect(meanIsUninformative(0.5, 72)).toBe(false);
  });

  it("rejects non-positive epsilon", () => {
    expect(() => countConfidenceRadius(0)).toThrow();
    expect(() => meanConfidenceRadius({ spread: 10, epsilon: 0, rows: 10, minDenominator: 10 })).toThrow();
  });

  it("defaults to a contribution bound of 1, reproducing one-row-per-person radii", () => {
    expect(countConfidenceRadius(1)).toBe(countConfidenceRadius(1, 1));
    expect(meanConfidenceRadius({ spread: 25, epsilon: 1, rows: 3, minDenominator: 10 }))
      .toBe(meanConfidenceRadius({ spread: 25, epsilon: 1, rows: 3, minDenominator: 10, maxContributions: 1 }));
  });

  it("scales count radius linearly with the contribution bound", () => {
    expect(countConfidenceRadius(1, 5)).toBeCloseTo(countConfidenceRadius(1) * 5, 10);
    expect(countConfidenceRadius(0.5, 3)).toBeCloseTo(countConfidenceRadius(0.5) * 3, 10);
  });

  it("scales the mean's sum-noise radius linearly with the contribution bound", () => {
    const base = meanConfidenceRadius({ spread: 25, epsilon: 1, rows: 3, minDenominator: 10 });
    const bounded = meanConfidenceRadius({ spread: 25, epsilon: 1, rows: 3, minDenominator: 10, maxContributions: 4 });
    expect(bounded).toBeCloseTo(base * 4, 10);
  });

  it("rejects a non-positive-integer contribution bound", () => {
    expect(() => countConfidenceRadius(1, 0)).toThrow();
    expect(() => countConfidenceRadius(1, 1.5)).toThrow();
    expect(() => meanConfidenceRadius({ spread: 10, epsilon: 1, rows: 10, minDenominator: 10, maxContributions: 0 })).toThrow();
  });
});

describe("suppression threshold under contribution bounding", () => {
  it("defaults to a bound of 1, matching the pinned server value", () => {
    // executor.py: min_group_size + confidence_radius(epsilon / 2, k)
    expect(groupSuppressionThreshold(5, 1)).toBeCloseTo(10.99, 2);
    expect(groupSuppressionThreshold(5, 1)).toBe(groupSuppressionThreshold(5, 1, 1));
  });

  it("scales the margin — not the policy minimum — with the contribution bound", () => {
    // Only the noise margin scales; min_group_size is a policy floor, not a
    // sensitivity. Showing a narrower threshold than the server applies would
    // mislead the analyst about which groups survive.
    const base = groupSuppressionThreshold(5, 1);
    const bounded = groupSuppressionThreshold(5, 1, 3);
    expect(bounded - 5).toBeCloseTo((base - 5) * 3, 10);
  });

  it("always exceeds the policy minimum for any bound", () => {
    for (const k of [1, 2, 5, 10]) {
      expect(groupSuppressionThreshold(5, 0.5, k)).toBeGreaterThan(5);
    }
  });
});
