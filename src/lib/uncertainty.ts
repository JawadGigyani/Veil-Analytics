/**
 * Client-side estimates of release uncertainty.
 *
 * These mirror the worker's mechanisms so the composer can tell an analyst
 * what a release will cost them in accuracy *before* they spend budget --
 * budget is reserved before execution and is not refunded.
 *
 * Every formula here must stay in step with its counterpart in
 * `packages/dp-core/dp_core/mechanisms/laplace.py` and
 * `services/analytics-worker/app/executor.py`. `uncertainty.test.ts` pins the
 * numbers against values computed by the Python side.
 */

/** Two-sided 95% radius of Laplace(0, sensitivity / epsilon): b * ln(20). */
export const LAPLACE_95 = Math.log(20);

/**
 * Radius for a count or histogram bucket.
 *
 * Sensitivity is 1 only when the privacy unit is the row (add/remove one row
 * changes a count by at most 1). Once an entity can contribute up to
 * `maxContributions` rows, adding or removing that entity can move the count
 * by that many, so sensitivity -- and the noise scale -- must scale with it.
 * The default of 1 reproduces today's row-is-the-unit behaviour exactly.
 */
export function countConfidenceRadius(epsilon: number, maxContributions: number = 1): number {
  if (epsilon <= 0) throw new Error("epsilon must be positive");
  if (!Number.isInteger(maxContributions) || maxContributions < 1) throw new Error("maxContributions must be a positive integer");
  return (LAPLACE_95 * maxContributions) / epsilon;
}

/**
 * Indicative radius for a bounded mean.
 *
 * The mechanism splits epsilon in half -- one half for the noisy shifted sum,
 * one for the noisy count -- and divides by the noisy count floored at the
 * public minimum. Estimating with the full epsilon and the raw row count, as
 * an earlier version of the composer did, understates the real error by at
 * least a factor of two.
 *
 * This covers the sum-noise term only; the independent noise on the count
 * contributes further error. It is a scale, not a calibrated interval.
 *
 * The sum's sensitivity is `spread` only when the privacy unit is the row.
 * When an entity can contribute up to `maxContributions` rows, adding or
 * removing that entity can move the (bounded) sum by up to that many times
 * `spread`, so the sum-noise term scales with it. Default 1 reproduces
 * today's row-is-the-unit behaviour exactly.
 */
export function meanConfidenceRadius({
  spread,
  epsilon,
  rows,
  minDenominator,
  maxContributions = 1,
}: {
  spread: number;
  epsilon: number;
  rows: number;
  minDenominator: number;
  maxContributions?: number;
}): number {
  if (epsilon <= 0) throw new Error("epsilon must be positive");
  if (spread <= 0) throw new Error("public bounds must have positive width");
  if (!Number.isInteger(maxContributions) || maxContributions < 1) throw new Error("maxContributions must be a positive integer");
  const denominator = Math.max(minDenominator, rows, 1);
  return (LAPLACE_95 * spread * maxContributions) / ((epsilon / 2) * denominator);
}

/**
 * Effective group-suppression threshold.
 *
 * Comparing a noisy count against the policy minimum alone lets an empty
 * group through often -- at selection epsilon 0.2 the Laplace scale is 5, so a
 * true count of zero clears a threshold of 5 about 18% of the time. Requiring
 * the noisy count to clear the minimum by the 95% radius of the selection
 * noise makes the policy floor hold with roughly that confidence.
 */
export function groupSuppressionThreshold(
  minGroupSize: number,
  epsilon: number,
  maxContributions: number = 1,
): number {
  if (epsilon <= 0) throw new Error("epsilon must be positive");
  // The margin is the confidence radius of the *selection* noise, whose
  // sensitivity is the contribution bound — the executor computes
  // `confidence_radius(selection_epsilon, k)`. Omitting k here would show the
  // analyst a narrower threshold than the server actually applies.
  return minGroupSize + countConfidenceRadius(epsilon / 2, maxContributions);
}

/**
 * Whether a mean's noise is a large enough fraction of the public bound range
 * that the released number carries no usable information.
 */
export function meanIsUninformative(radius: number, spread: number): boolean {
  return spread > 0 && radius > spread * 0.2;
}
