"""Tests for the empirical epsilon estimator.

Uses modest sample counts to keep runtime low (the CLI default of 6000
samples per mechanism per epsilon is for the full report, not for tests).
`estimate_epsilon_from_samples` is a statistical lower bound, so a small
amount of run-to-run wobble is expected; thresholds carry margin against
that, verified against repeated manual runs (see module docstring probes).
"""

from __future__ import annotations

import numpy as np
import pytest
from dp_core.mechanisms.laplace import laplace_noise

from dp_audit.epsilon_estimation import (
    clopper_pearson_bounds,
    estimate_bounded_mean_epsilon,
    estimate_bounded_sum_epsilon,
    estimate_count_epsilon,
    estimate_epsilon_from_samples,
    run_epsilon_estimation_suite,
)

N_SAMPLES = 1000
N_BINS = 25


class TestClopperPearsonBounds:
    def test_interval_contains_the_point_estimate(self):
        lower, upper = clopper_pearson_bounds(50, 100, alpha=0.05)
        assert lower < 0.5 < upper

    def test_zero_count_gives_zero_lower_bound(self):
        lower, upper = clopper_pearson_bounds(0, 100, alpha=0.05)
        assert lower == 0.0
        assert upper > 0.0

    def test_full_count_gives_one_upper_bound(self):
        lower, upper = clopper_pearson_bounds(100, 100, alpha=0.05)
        assert upper == 1.0
        assert lower < 1.0

    def test_empty_sample_is_maximally_uninformative(self):
        assert clopper_pearson_bounds(0, 0, alpha=0.05) == (0.0, 1.0)


class TestEstimateEpsilonFromSamples:
    def test_identical_distributions_give_zero(self):
        samples = np.full(500, 7.0)
        assert estimate_epsilon_from_samples(samples, samples) == 0.0

    def test_completely_separated_distributions_give_a_large_estimate(self):
        samples_a = np.full(500, 0.0)
        samples_b = np.full(500, 100.0)
        estimate = estimate_epsilon_from_samples(samples_a, samples_b, n_bins=10)
        assert estimate > 3.5

    def test_estimate_is_never_negative(self):
        rng = np.random.default_rng(0)
        samples_a = rng.normal(0, 1, size=500)
        samples_b = rng.normal(0.01, 1, size=500)
        assert estimate_epsilon_from_samples(samples_a, samples_b) >= 0.0


@pytest.mark.parametrize("epsilon", [0.5, 1.0, 2.0])
class TestMechanismsStayWithinClaim:
    """The core acceptance check: correctly-calibrated mechanisms must not
    be flagged as exceeding their claimed epsilon beyond sampling error."""

    def test_private_count(self, epsilon):
        result = estimate_count_epsilon(epsilon, n_samples=N_SAMPLES, n_bins=N_BINS)
        assert result.within_claim, (
            f"private_count estimated {result.estimated_epsilon:.3f} "
            f"> claimed {epsilon} beyond tolerance"
        )

    def test_private_bounded_sum(self, epsilon):
        result = estimate_bounded_sum_epsilon(epsilon, n_samples=N_SAMPLES, n_bins=N_BINS)
        assert result.within_claim, (
            f"private_bounded_sum estimated {result.estimated_epsilon:.3f} "
            f"> claimed {epsilon} beyond tolerance"
        )

    def test_private_bounded_mean(self, epsilon):
        result = estimate_bounded_mean_epsilon(epsilon, n_samples=N_SAMPLES, n_bins=N_BINS)
        assert result.within_claim, (
            f"private_bounded_mean estimated {result.estimated_epsilon:.3f} "
            f"> claimed {epsilon} beyond tolerance"
        )


class TestSuite:
    def test_suite_covers_all_three_mechanisms_per_epsilon(self):
        epsilons = [0.5, 1.0]
        results = run_epsilon_estimation_suite(epsilons, n_samples=N_SAMPLES, n_bins=N_BINS)
        assert len(results) == 3 * len(epsilons)
        mechanisms = {r.mechanism for r in results}
        assert mechanisms == {"private_count", "private_bounded_sum", "private_bounded_mean"}

    def test_suite_all_within_claim(self):
        results = run_epsilon_estimation_suite([0.5, 1.0, 2.0], n_samples=N_SAMPLES, n_bins=N_BINS)
        assert all(r.within_claim for r in results)


class TestDetectsAMiscalibratedMechanism:
    """This is the check that catches a mis-calibrated mechanism -- the
    whole point of this module. A mechanism that uses less noise than its
    claimed epsilon requires must be flagged, not silently accepted.
    """

    @staticmethod
    def _broken_private_count(value: int, claimed_epsilon: float, factor: float = 3.0) -> int:
        # Deliberately spends 3x the claimed epsilon's worth of budget (i.e.
        # adds a third of the required noise) to simulate a calibration bug.
        actual_epsilon = claimed_epsilon * factor
        return max(0, round(value + laplace_noise(1 / actual_epsilon)))

    def test_undernoised_mechanism_is_flagged(self):
        claimed_epsilon = 1.0
        samples_a = np.array(
            [self._broken_private_count(100, claimed_epsilon) for _ in range(N_SAMPLES)],
            dtype=float,
        )
        samples_b = np.array(
            [self._broken_private_count(101, claimed_epsilon) for _ in range(N_SAMPLES)],
            dtype=float,
        )
        estimated = estimate_epsilon_from_samples(samples_a, samples_b, n_bins=N_BINS)
        assert estimated > claimed_epsilon + 0.05
