"""Unit and statistical tests for the discrete Laplace (geometric) mechanism."""

from __future__ import annotations

import math
from collections import Counter

import pytest

from dp_core.mechanisms import discrete as disc


def _theoretical_pmf(k: int, scale: float) -> float:
    """P(X = k) for the two-sided geometric with the given scale."""
    t = math.exp(-1.0 / scale)
    return ((1 - t) / (1 + t)) * t ** abs(k)


class TestDiscreteLaplaceNoise:
    def test_rejects_non_positive_scale(self):
        with pytest.raises(ValueError, match="Scale must be positive"):
            disc.discrete_laplace_noise(0)
        with pytest.raises(ValueError, match="Scale must be positive"):
            disc.discrete_laplace_noise(-1.0)

    def test_returns_int(self):
        for _ in range(50):
            assert isinstance(disc.discrete_laplace_noise(1.0), int)

    def test_different_calls_produce_different_values(self):
        values = {disc.discrete_laplace_noise(2.0) for _ in range(50)}
        assert len(values) > 1

    def test_can_be_negative(self):
        # With enough draws at a reasonably wide scale, both signs must appear.
        values = [disc.discrete_laplace_noise(3.0) for _ in range(300)]
        assert any(v < 0 for v in values)
        assert any(v > 0 for v in values)

    @pytest.mark.parametrize("scale", [0.5, 1.0, 2.0, 5.0])
    def test_matches_theoretical_distribution_total_variation(self, scale):
        """The empirical PMF over many draws must match the closed-form PMF.

        Uses total variation distance rather than a chi-square goodness-of-fit
        test. A first attempt at this test used chi-square over a fixed
        window of individual integers, but the discrete Laplace's tail decays
        fast enough that far-out bins have expected counts well under 1 --
        chi-square requires roughly >= 5 per bin to be valid -- so a single
        rare (but correctly-distributed) tail draw would occasionally send
        the statistic to an absurd value having nothing to do with a real
        mismatch (observed empirically: p-values as low as 1e-110 from a
        sampler that direct PMF comparison showed was correct). TV distance
        over a window sized to the scale, with everything outside it pooled
        into a single "elsewhere" bucket on both sides, does not have that
        failure mode.
        """
        n_samples = 50_000
        t = math.exp(-1.0 / scale)
        p0 = (1 - t) / (1 + t)

        # Grow the window until the true tail mass beyond it is negligible,
        # so pooling it into one bucket does not itself introduce bias.
        window_radius = 1
        while 2 * p0 * t ** (window_radius + 1) / (1 - t) > 1e-4:
            window_radius += 1

        samples = [disc.discrete_laplace_noise(scale) for _ in range(n_samples)]
        counts = Counter(samples)

        window = range(-window_radius, window_radius + 1)
        empirical = {k: counts.get(k, 0) / n_samples for k in window}
        theoretical = {k: _theoretical_pmf(k, scale) for k in window}
        empirical["elsewhere"] = 1 - sum(empirical.values())
        theoretical["elsewhere"] = 1 - sum(theoretical.values())

        total_variation = 0.5 * sum(
            abs(empirical[k] - theoretical[k]) for k in empirical
        )
        # At n=50,000 the sampling error on a correct implementation stays
        # under ~0.015 across these scales (measured empirically over 240
        # repeated runs); 0.03 leaves a comfortable margin against flakiness
        # while still catching a materially wrong distribution.
        assert total_variation < 0.03, (
            f"total variation distance too large for scale={scale}: "
            f"{total_variation:.4f}"
        )


class TestPrivateCountDiscrete:
    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            disc.private_count_discrete(10, 0)

    def test_returns_int(self):
        for _ in range(50):
            assert isinstance(disc.private_count_discrete(10, 0.5), int)

    def test_never_negative(self):
        for _ in range(200):
            assert disc.private_count_discrete(1, 0.1) >= 0

    @pytest.mark.parametrize("noise", [-10_000, -1, 0, 1, 10_000])
    def test_always_non_negative_with_extreme_noise(self, monkeypatch, noise):
        monkeypatch.setattr(disc, "discrete_laplace_noise", lambda scale: noise)
        assert disc.private_count_discrete(5, 1.0) >= 0

    def test_no_noise_returns_exact(self, monkeypatch):
        monkeypatch.setattr(disc, "discrete_laplace_noise", lambda scale: 0)
        assert disc.private_count_discrete(42, 1.0) == 42


class TestInternalSamplers:
    """White-box checks on the exact-arithmetic building blocks."""

    def test_sample_bernoulli_boundary_cases(self):
        assert disc._sample_bernoulli(0, 5) is False
        assert disc._sample_bernoulli(5, 5) is True

    def test_sample_bernoulli_matches_expected_rate(self):
        n = 20_000
        successes = sum(1 for _ in range(n) if disc._sample_bernoulli(1, 4))
        rate = successes / n
        assert 0.20 < rate < 0.30  # true rate 0.25

    def test_sample_bernoulli_exp1_matches_exp(self):
        n = 20_000
        # Bernoulli(exp(-1/2)) should succeed with probability e^-0.5 ~ 0.6065
        successes = sum(1 for _ in range(n) if disc._sample_bernoulli_exp1(1, 2))
        rate = successes / n
        expected = math.exp(-0.5)
        assert abs(rate - expected) < 0.02

    def test_sample_geometric_mean_matches_theory(self):
        # Geometric(1 - exp(-p/q)) on {0,1,2,...} has mean t/(1-t), t=exp(-p/q).
        p, q = 1, 2  # t = exp(-0.5)
        t = math.exp(-p / q)
        expected_mean = t / (1 - t)
        n = 20_000
        samples = [disc._sample_geometric(p, q) for _ in range(n)]
        empirical_mean = sum(samples) / n
        assert abs(empirical_mean - expected_mean) < 0.1 * expected_mean + 0.05
