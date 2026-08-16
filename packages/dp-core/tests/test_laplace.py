"""Unit tests for the Laplace mechanism."""

import math

import pytest

from dp_core.mechanisms import laplace as lap


class TestLaplaceNoise:
    def test_returns_finite_value(self):
        assert math.isfinite(lap.laplace_noise(1.0))

    def test_rejects_non_positive_scale(self):
        with pytest.raises(ValueError, match="Scale must be positive"):
            lap.laplace_noise(0)

    def test_different_calls_produce_different_values(self):
        values = {lap.laplace_noise(1.0) for _ in range(20)}
        assert len(values) > 1


class TestPrivateCount:
    def test_non_negative(self):
        for _ in range(50):
            assert lap.private_count(3, 0.5) >= 0

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            lap.private_count(10, 0)

    @pytest.mark.parametrize("noise", [-10_000, -1, 0, 1, 10_000])
    def test_always_non_negative_with_extreme_noise(self, monkeypatch, noise):
        monkeypatch.setattr(lap, "laplace_noise", lambda s: float(noise))
        assert lap.private_count(5, 1.0) >= 0


class TestPrivateBoundedSum:
    def test_rejects_invalid_bounds(self):
        with pytest.raises(ValueError):
            lap.private_bounded_sum(100, 1.0, 50, 50)

    def test_returns_finite(self):
        result = lap.private_bounded_sum(100.0, 1.0, 0, 200)
        assert math.isfinite(result)

    def test_no_noise_returns_exact(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda s: 0.0)
        assert lap.private_bounded_sum(42.0, 1.0, 0, 100) == 42.0


class TestPrivateBoundedMean:
    def test_stays_within_bounds(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda s: 10_000.0)
        result = lap.private_bounded_mean(500, 10, 1.0, 0, 100, 10)
        assert 0 <= result <= 100

    def test_stays_within_bounds_negative_noise(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda s: -10_000.0)
        result = lap.private_bounded_mean(500, 10, 1.0, 0, 100, 10)
        assert 0 <= result <= 100

    def test_no_noise_returns_expected(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda s: 0.0)
        # total=200, count=10, lower=0, upper=100 → mean=20
        result = lap.private_bounded_mean(200, 10, 1.0, 0, 100, 10)
        assert result == 20.0


class TestConfidenceRadius:
    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            lap.confidence_radius(0)

    def test_decreases_with_epsilon(self):
        r1 = lap.confidence_radius(0.5)
        r2 = lap.confidence_radius(1.0)
        assert r1 > r2


class TestBoundedMeanRelease:
    """The release form exposes the denominator the mechanism actually used.

    Without it a caller cannot compute an honest uncertainty figure, because
    the error in the released mean scales as 1 / denominator and the
    denominator is a noisy quantity the caller never sees.
    """

    def test_release_matches_the_plain_value_shape(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda scale: 0.0)
        release = lap.private_bounded_mean_release(300.0, 10, 1.0, 0.0, 100.0, 10)
        assert release.value == 30.0

    def test_denominator_is_floored_at_the_public_minimum(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda scale: 0.0)
        release = lap.private_bounded_mean_release(30.0, 3, 1.0, 0.0, 100.0, 10)
        # True count is 3, so the public floor of 10 is what divides.
        assert release.denominator == 10.0

    def test_radius_scales_inversely_with_the_denominator(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda scale: 0.0)
        small = lap.private_bounded_mean_release(30.0, 3, 1.0, 0.0, 100.0, 10)
        large = lap.private_bounded_mean_release(3000.0, 300, 1.0, 0.0, 100.0, 10)
        assert large.confidence_radius < small.confidence_radius

    def test_radius_scales_inversely_with_epsilon(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda scale: 0.0)
        cheap = lap.private_bounded_mean_release(3000.0, 300, 0.2, 0.0, 100.0, 10)
        rich = lap.private_bounded_mean_release(3000.0, 300, 2.0, 0.0, 100.0, 10)
        assert rich.confidence_radius < cheap.confidence_radius

    def test_wrapper_returns_only_the_value(self, monkeypatch):
        monkeypatch.setattr(lap, "laplace_noise", lambda scale: 0.0)
        value = lap.private_bounded_mean(300.0, 10, 1.0, 0.0, 100.0, 10)
        assert isinstance(value, float)
        assert value == 30.0
