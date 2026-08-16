"""Empirical epsilon estimation from sampled output distributions.

For each mechanism, sample its output many times on a pair of neighbouring
datasets -- two inputs differing in exactly one row, the same adjacency
`dp_core`'s sensitivity analysis assumes throughout. Under epsilon-DP, every
measurable event's probability ratio between the two neighbours is bounded
by exp(epsilon). Histogramming the two output samples and taking the
largest observed |log(P_a / P_b)| over bins estimates that bound directly
from data, without needing to know the mechanism's noise distribution in
closed form.

Raw bin proportions are a biased estimator of this quantity: a bin that
happens to get zero hits in one sample yields an infinite ratio purely from
finite-sample luck, not from a real violation. This module corrects for
that with a Clopper-Pearson confidence interval on each bin's proportion
(Bonferroni-corrected across bins and both comparison directions), and uses
the *conservative* side of the interval -- the lower bound on the numerator
proportion, the upper bound on the denominator proportion -- so the
resulting estimate is, with high probability, no larger than the
mechanism's true privacy loss.

IMPORTANT -- this is a LOWER-BOUND estimator, not a certification. If the
estimate exceeds the claimed epsilon (beyond the stated confidence's
sampling error), that is definite evidence of mis-calibration: an event was
observed whose empirical probability ratio, even after the conservative
correction, still exceeds what the claimed epsilon permits. If the estimate
does NOT exceed the claimed epsilon, that proves nothing about correctness
-- it only means this particular audit, with this many samples and this
binning, did not catch a violation. A subtler miscalibration, a
rarer-but-still-too-likely event, or simply more samples could still find
one. Use it to catch bugs, not to sign off on a mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import beta as beta_dist

from dp_core import private_bounded_mean, private_bounded_sum, private_count


def clopper_pearson_bounds(count: int, n: int, alpha: float) -> tuple[float, float]:
    """Two-sided (1 - alpha) Clopper-Pearson interval for a binomial proportion.

    Exact (not normal-approximation) bounds, which matter here because the
    bins that carry the most information about epsilon are exactly the
    rare-event bins where a normal approximation is least trustworthy.
    """
    if n == 0:
        return 0.0, 1.0
    lower = float(beta_dist.ppf(alpha / 2, count, n - count + 1)) if count > 0 else 0.0
    upper = float(beta_dist.ppf(1 - alpha / 2, count + 1, n - count)) if count < n else 1.0
    return lower, upper


def estimate_epsilon_from_samples(
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    alpha: float = 0.05,
    n_bins: int = 50,
) -> float:
    """Empirical lower bound on the max-divergence between two output samples.

    See the module docstring for what this is and is not a bound on.
    """
    combined = np.concatenate([samples_a, samples_b])
    lo, hi = float(combined.min()), float(combined.max())
    if lo == hi:
        # Every draw landed on the same value: no divergence is observable.
        return 0.0
    edges = np.linspace(lo, hi + 1e-9, n_bins + 1)
    counts_a, _ = np.histogram(samples_a, bins=edges)
    counts_b, _ = np.histogram(samples_b, bins=edges)
    n_a, n_b = len(samples_a), len(samples_b)

    # Bonferroni correction: n_bins bins, each checked in both directions
    # (a-vs-b and b-vs-a), so 2 * n_bins simultaneous confidence claims.
    corrected_alpha = alpha / (2 * n_bins)

    best = 0.0
    for c_a, c_b in zip(counts_a, counts_b):
        lower_a, upper_a = clopper_pearson_bounds(int(c_a), n_a, corrected_alpha)
        lower_b, upper_b = clopper_pearson_bounds(int(c_b), n_b, corrected_alpha)
        if lower_a > 0 and upper_b > 0:
            best = max(best, np.log(lower_a / upper_b))
        if lower_b > 0 and upper_a > 0:
            best = max(best, np.log(lower_b / upper_a))
    return float(best)


@dataclass(frozen=True)
class EpsilonEstimate:
    """Outcome of one empirical epsilon audit for a single mechanism."""

    mechanism: str
    claimed_epsilon: float
    estimated_epsilon: float
    n_samples: int
    within_claim: bool  # estimated_epsilon <= claimed_epsilon (+ tolerance)


def _make_estimate(
    mechanism: str,
    claimed_epsilon: float,
    samples_a: np.ndarray,
    samples_b: np.ndarray,
    alpha: float,
    n_bins: int,
    tolerance: float,
) -> EpsilonEstimate:
    estimated = estimate_epsilon_from_samples(samples_a, samples_b, alpha=alpha, n_bins=n_bins)
    return EpsilonEstimate(
        mechanism=mechanism,
        claimed_epsilon=claimed_epsilon,
        estimated_epsilon=estimated,
        n_samples=len(samples_a),
        within_claim=estimated <= claimed_epsilon + tolerance,
    )


def estimate_count_epsilon(
    epsilon: float,
    value: int = 100,
    n_samples: int = 4000,
    alpha: float = 0.05,
    n_bins: int = 50,
    tolerance: float = 0.05,
) -> EpsilonEstimate:
    """Audit `private_count` on neighbours differing by one row (count, count+1)."""
    samples_a = np.array([private_count(value, epsilon) for _ in range(n_samples)], dtype=float)
    samples_b = np.array([private_count(value + 1, epsilon) for _ in range(n_samples)], dtype=float)
    return _make_estimate("private_count", epsilon, samples_a, samples_b, alpha, n_bins, tolerance)


def estimate_bounded_sum_epsilon(
    epsilon: float,
    value: float = 50.0,
    lower: float = 0.0,
    upper: float = 10.0,
    n_samples: int = 4000,
    alpha: float = 0.05,
    n_bins: int = 50,
    tolerance: float = 0.05,
) -> EpsilonEstimate:
    """Audit `private_bounded_sum` on neighbours differing by one row at the bound.

    The added/removed row contributes its clipped extreme value (``upper``),
    which is the worst-case, maximum-sensitivity single-row change the
    mechanism is calibrated against.
    """
    samples_a = np.array(
        [private_bounded_sum(value, epsilon, lower, upper) for _ in range(n_samples)]
    )
    samples_b = np.array(
        [private_bounded_sum(value + upper, epsilon, lower, upper) for _ in range(n_samples)]
    )
    return _make_estimate(
        "private_bounded_sum", epsilon, samples_a, samples_b, alpha, n_bins, tolerance
    )


def estimate_bounded_mean_epsilon(
    epsilon: float,
    total: float = 15.0,
    count: int = 3,
    lower: float = 0.0,
    upper: float = 10.0,
    minimum_denominator: int = 1,
    n_samples: int = 4000,
    alpha: float = 0.05,
    n_bins: int = 50,
    tolerance: float = 0.05,
) -> EpsilonEstimate:
    """Audit `private_bounded_mean` on neighbours differing by one row.

    A small ``count`` is chosen deliberately: the mean's sensitivity to a
    single row shrinks as the group grows (the row change is diluted by the
    denominator), so a small group is the regime where a mis-calibration is
    easiest to see and hardest to hide.
    """
    samples_a = np.array(
        [
            private_bounded_mean(total, count, epsilon, lower, upper, minimum_denominator)
            for _ in range(n_samples)
        ]
    )
    samples_b = np.array(
        [
            private_bounded_mean(
                total + upper, count + 1, epsilon, lower, upper, minimum_denominator
            )
            for _ in range(n_samples)
        ]
    )
    return _make_estimate(
        "private_bounded_mean", epsilon, samples_a, samples_b, alpha, n_bins, tolerance
    )


def run_epsilon_estimation_suite(
    epsilons: list[float],
    n_samples: int = 4000,
    alpha: float = 0.05,
    n_bins: int = 50,
    tolerance: float = 0.05,
) -> list[EpsilonEstimate]:
    """Audit all three release-path mechanisms at each claimed epsilon."""
    results: list[EpsilonEstimate] = []
    for epsilon in epsilons:
        results.append(estimate_count_epsilon(epsilon, n_samples=n_samples, alpha=alpha, n_bins=n_bins, tolerance=tolerance))
        results.append(estimate_bounded_sum_epsilon(epsilon, n_samples=n_samples, alpha=alpha, n_bins=n_bins, tolerance=tolerance))
        results.append(estimate_bounded_mean_epsilon(epsilon, n_samples=n_samples, alpha=alpha, n_bins=n_bins, tolerance=tolerance))
    return results
