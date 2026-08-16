"""Statistical distribution tests for dp-core mechanisms.

These tests run many trials and verify that the observed noise
distributions match the expected statistical properties.
"""

import math

import pytest

from dp_core.mechanisms.laplace import laplace_noise, private_count
from dp_core.mechanisms.gaussian import gaussian_noise, calibrate_sigma, private_count_gaussian
from dp_core.mechanisms.randomized_response import (
    aggregate_randomized_responses,
    randomized_response,
)

TRIALS = 500


class TestLaplaceDistribution:
    def test_mean_absolute_error_converges(self):
        """Laplace(0, b) has E[|X|] = b.  For scale = 1/ε with ε=1, b=1."""
        epsilon = 1.0
        scale = 1.0 / epsilon
        errors = [abs(laplace_noise(scale)) for _ in range(TRIALS)]
        mae = sum(errors) / len(errors)
        # Expected MAE = scale = 1.0, allow generous tolerance
        assert 0.5 < mae < 2.0

    def test_private_count_unbiased(self):
        """Average private count should be close to the true count."""
        true_count = 1000
        epsilon = 1.0
        outputs = [private_count(true_count, epsilon) for _ in range(TRIALS)]
        avg = sum(outputs) / len(outputs)
        # Within 3 standard deviations of expected
        assert abs(avg - true_count) < 15

    def test_higher_epsilon_gives_less_noise(self):
        """Higher ε → smaller noise magnitude."""
        scale_low = 1.0 / 0.5
        scale_high = 1.0 / 2.0
        mae_low = sum(abs(laplace_noise(scale_low)) for _ in range(TRIALS)) / TRIALS
        mae_high = sum(abs(laplace_noise(scale_high)) for _ in range(TRIALS)) / TRIALS
        assert mae_high < mae_low

    def test_95th_percentile_coverage(self):
        """The 95th percentile of |Laplace(0,b)| should be near b·ln(20)."""
        epsilon = 1.0
        scale = 1.0 / epsilon
        expected_p95 = scale * math.log(20)  # ≈ 2.996
        errors = sorted(abs(laplace_noise(scale)) for _ in range(TRIALS))
        observed_p95 = errors[int(0.95 * len(errors))]
        # Allow 50% tolerance
        assert expected_p95 * 0.5 < observed_p95 < expected_p95 * 2.0


class TestGaussianDistribution:
    def test_mean_is_near_zero(self):
        """Gaussian noise should be centered at 0."""
        sigma = 2.0
        values = [gaussian_noise(sigma) for _ in range(TRIALS)]
        mean = sum(values) / len(values)
        # Within 3σ/√n of zero
        tolerance = 3 * sigma / math.sqrt(TRIALS)
        assert abs(mean) < tolerance

    def test_variance_is_near_sigma_squared(self):
        """Observed variance should be close to σ²."""
        sigma = 3.0
        values = [gaussian_noise(sigma) for _ in range(TRIALS)]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        # Within factor of 2
        assert sigma**2 * 0.5 < variance < sigma**2 * 2.0

    def test_gaussian_count_unbiased(self):
        """Average Gaussian private count should be close to true count."""
        true_count = 500
        epsilon, delta = 1.0, 1e-5
        outputs = [private_count_gaussian(true_count, epsilon, delta) for _ in range(TRIALS)]
        avg = sum(outputs) / len(outputs)
        sigma = calibrate_sigma(1.0, epsilon, delta)
        tolerance = 3 * sigma / math.sqrt(TRIALS) + 5
        assert abs(avg - true_count) < tolerance


class TestRandomizedResponseDistribution:
    def test_debiased_estimates_converge(self):
        """De-biased RR estimates should approximate true frequencies."""
        domain = ["a", "b", "c"]
        true_counts = {"a": 500, "b": 300, "c": 200}
        epsilon = 2.0

        # Simulate collection
        responses = []
        for val, n in true_counts.items():
            for _ in range(n):
                responses.append(randomized_response(val, domain, epsilon))

        estimated = aggregate_randomized_responses(responses, domain, epsilon)
        total = sum(true_counts.values())

        for val in domain:
            true_frac = true_counts[val] / total
            est_frac = estimated[val] / total
            # Within 15% of true fraction
            assert abs(true_frac - est_frac) < 0.15, (
                f"{val}: true={true_frac:.3f}, est={est_frac:.3f}"
            )

    def test_high_epsilon_preserves_order(self):
        """With high ε, RR should preserve the ranking of categories."""
        domain = ["rare", "common", "very_common"]
        true_counts = {"rare": 50, "common": 200, "very_common": 500}
        epsilon = 5.0

        responses = []
        for val, n in true_counts.items():
            for _ in range(n):
                responses.append(randomized_response(val, domain, epsilon))

        estimated = aggregate_randomized_responses(responses, domain, epsilon)
        assert estimated["very_common"] > estimated["common"] > estimated["rare"]
