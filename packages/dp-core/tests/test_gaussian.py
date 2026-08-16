"""Unit tests for the Gaussian mechanism."""

import math

import pytest

from dp_core.mechanisms import gaussian as gauss


class TestCalibrateSigma:
    def test_positive_sigma(self):
        sigma = gauss.calibrate_sigma(1.0, 1.0, 1e-5)
        assert sigma > 0

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            gauss.calibrate_sigma(1.0, 0, 1e-5)

    def test_rejects_invalid_delta(self):
        with pytest.raises(ValueError):
            gauss.calibrate_sigma(1.0, 1.0, 0)
        with pytest.raises(ValueError):
            gauss.calibrate_sigma(1.0, 1.0, 1.0)

    def test_higher_epsilon_gives_lower_sigma(self):
        s1 = gauss.calibrate_sigma(1.0, 0.5, 1e-5)
        s2 = gauss.calibrate_sigma(1.0, 1.0, 1e-5)
        assert s1 > s2

    def test_higher_sensitivity_gives_higher_sigma(self):
        s1 = gauss.calibrate_sigma(1.0, 1.0, 1e-5)
        s2 = gauss.calibrate_sigma(2.0, 1.0, 1e-5)
        assert s2 > s1

    def test_rejects_epsilon_above_one(self):
        """The classical bound is unproven above epsilon = 1.

        Calibrating with it there returns a sigma that does not satisfy
        (epsilon, delta)-DP, so it must fail loudly rather than release.
        """
        with pytest.raises(ValueError, match="only valid for epsilon"):
            gauss.calibrate_sigma(1.0, 1.5, 1e-5)

    def test_accepts_epsilon_exactly_one(self):
        assert gauss.calibrate_sigma(1.0, gauss.MAX_CLASSICAL_EPSILON, 1e-5) > 0

    def test_dependent_mechanisms_inherit_the_restriction(self):
        with pytest.raises(ValueError, match="only valid for epsilon"):
            gauss.private_count_gaussian(10, 2.0, 1e-5)
        with pytest.raises(ValueError, match="only valid for epsilon"):
            gauss.private_sum_gaussian(10.0, 2.0, 1e-5, 0.0, 100.0)


class TestGaussianNoise:
    def test_returns_finite(self):
        assert math.isfinite(gauss.gaussian_noise(1.0))

    def test_rejects_non_positive_sigma(self):
        with pytest.raises(ValueError):
            gauss.gaussian_noise(0)

    def test_produces_varied_output(self):
        values = {gauss.gaussian_noise(1.0) for _ in range(20)}
        assert len(values) > 1


class TestPrivateCountGaussian:
    def test_non_negative(self):
        for _ in range(50):
            assert gauss.private_count_gaussian(5, 1.0, 1e-5) >= 0

    def test_rejects_bad_params(self):
        with pytest.raises(ValueError):
            gauss.private_count_gaussian(5, 0, 1e-5)


class TestPrivateSumGaussian:
    def test_returns_finite(self):
        result = gauss.private_sum_gaussian(100, 1.0, 1e-5, 0, 200)
        assert math.isfinite(result)

    def test_rejects_invalid_bounds(self):
        with pytest.raises(ValueError):
            gauss.private_sum_gaussian(100, 1.0, 1e-5, 50, 50)


class TestGaussianConfidenceRadius:
    def test_positive(self):
        r = gauss.gaussian_confidence_radius(1.0)
        assert r > 0

    def test_increases_with_sigma(self):
        r1 = gauss.gaussian_confidence_radius(1.0)
        r2 = gauss.gaussian_confidence_radius(2.0)
        assert r2 > r1


class TestCalibrateSigmaAnalytic:
    """Balle & Wang (2018) analytic Gaussian calibration.

    Valid for all epsilon > 0, unlike the classical bound in
    ``TestCalibrateSigma`` which refuses epsilon > 1.
    """

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError, match="Epsilon must be positive"):
            gauss.calibrate_sigma_analytic(1.0, 0, 1e-5)

    def test_rejects_invalid_delta(self):
        with pytest.raises(ValueError):
            gauss.calibrate_sigma_analytic(1.0, 1.0, 0)
        with pytest.raises(ValueError):
            gauss.calibrate_sigma_analytic(1.0, 1.0, 1.0)

    def test_rejects_non_positive_sensitivity(self):
        with pytest.raises(ValueError, match="Sensitivity must be positive"):
            gauss.calibrate_sigma_analytic(0, 1.0, 1e-5)

    @pytest.mark.parametrize("epsilon", [0.01, 0.1, 0.5, 1.0])
    def test_at_or_below_classical_for_epsilon_up_to_one(self, epsilon):
        """Analytic calibration is provably at least as tight as the
        classical bound wherever the classical bound is valid (eps <= 1)."""
        classical = gauss.calibrate_sigma(1.0, epsilon, 1e-5)
        analytic = gauss.calibrate_sigma_analytic(1.0, epsilon, 1e-5)
        assert analytic <= classical

    @pytest.mark.parametrize("epsilon", [2.0, 5.0, 10.0])
    def test_valid_for_epsilon_above_one_where_classical_refuses(self, epsilon):
        with pytest.raises(ValueError, match="only valid for epsilon"):
            gauss.calibrate_sigma(1.0, epsilon, 1e-5)

        sigma = gauss.calibrate_sigma_analytic(1.0, epsilon, 1e-5)
        assert sigma > 0
        assert math.isfinite(sigma)

    @pytest.mark.parametrize("epsilon", [0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0])
    @pytest.mark.parametrize("delta", [1e-5, 1e-6, 1e-8])
    def test_satisfies_the_privacy_profile(self, epsilon, delta):
        """The whole point of calibration: plugging sigma back into the
        exact privacy profile must not exceed the requested delta."""
        sensitivity = 1.0
        sigma = gauss.calibrate_sigma_analytic(sensitivity, epsilon, delta)
        achieved_delta = gauss._gaussian_privacy_profile_delta(
            sigma, sensitivity, epsilon
        )
        # Tiny slack for the binary search's finite precision.
        assert achieved_delta <= delta * (1 + 1e-6) + 1e-15

    def test_higher_epsilon_gives_lower_sigma(self):
        s1 = gauss.calibrate_sigma_analytic(1.0, 0.5, 1e-5)
        s2 = gauss.calibrate_sigma_analytic(1.0, 5.0, 1e-5)
        assert s2 < s1

    def test_higher_sensitivity_gives_higher_sigma(self):
        s1 = gauss.calibrate_sigma_analytic(1.0, 1.0, 1e-5)
        s2 = gauss.calibrate_sigma_analytic(2.0, 1.0, 1e-5)
        assert s2 > s1


class TestPrivateCountGaussianAnalytic:
    """The release function the analytics worker calls for count-shaped
    Gaussian releases (count, grouped_count, top_k, histogram buckets)."""

    def test_non_negative(self):
        for _ in range(50):
            value, sigma = gauss.private_count_gaussian_analytic(5, 1.0, 1e-5)
            assert value >= 0
            assert sigma > 0

    def test_sigma_matches_calibrate_sigma_analytic_exactly(self):
        value, sigma = gauss.private_count_gaussian_analytic(10, 0.7, 1e-6)
        assert sigma == gauss.calibrate_sigma_analytic(1.0, 0.7, 1e-6)

    def test_valid_above_epsilon_one_where_classical_would_refuse(self):
        """The whole point of using the analytic calibration on the release
        path: epsilon > 1 must not raise here even though calibrate_sigma
        (classical) refuses it."""
        value, sigma = gauss.private_count_gaussian_analytic(10, 3.5, 1e-5)
        assert sigma == gauss.calibrate_sigma_analytic(1.0, 3.5, 1e-5)
        assert math.isfinite(sigma) and sigma > 0

    def test_sigma_scales_with_max_contributions(self):
        _, sigma_k1 = gauss.private_count_gaussian_analytic(10, 1.0, 1e-5, max_contributions=1)
        _, sigma_k5 = gauss.private_count_gaussian_analytic(10, 1.0, 1e-5, max_contributions=5)
        assert sigma_k5 == gauss.calibrate_sigma_analytic(5.0, 1.0, 1e-5)
        assert sigma_k5 > sigma_k1

    def test_rejects_non_positive_max_contributions(self):
        with pytest.raises(ValueError, match="max_contributions"):
            gauss.private_count_gaussian_analytic(10, 1.0, 1e-5, max_contributions=0)


class TestPrivateSumGaussianAnalytic:
    """The release function the analytics worker calls for bounded_sum
    Gaussian releases."""

    def test_returns_finite(self):
        value, sigma = gauss.private_sum_gaussian_analytic(100, 1.0, 1e-5, 0, 200)
        assert math.isfinite(value)
        assert sigma > 0

    def test_rejects_invalid_bounds(self):
        with pytest.raises(ValueError):
            gauss.private_sum_gaussian_analytic(100, 1.0, 1e-5, 50, 50)

    def test_sigma_matches_calibrate_sigma_analytic_with_l2_sensitivity(self):
        # sensitivity = max(|lower|, |upper|) = max(20, 80) = 80
        value, sigma = gauss.private_sum_gaussian_analytic(100, 0.9, 1e-6, -20, 80)
        assert sigma == gauss.calibrate_sigma_analytic(80.0, 0.9, 1e-6)

    def test_valid_above_epsilon_one_where_classical_would_refuse(self):
        value, sigma = gauss.private_sum_gaussian_analytic(100, 4.0, 1e-5, 0, 200)
        assert sigma == gauss.calibrate_sigma_analytic(200.0, 4.0, 1e-5)

    def test_sigma_scales_with_max_contributions(self):
        # sensitivity = k * max(|lower|, |upper|)
        _, sigma_k1 = gauss.private_sum_gaussian_analytic(
            100, 1.0, 1e-5, 0, 20, max_contributions=1,
        )
        _, sigma_k4 = gauss.private_sum_gaussian_analytic(
            100, 1.0, 1e-5, 0, 20, max_contributions=4,
        )
        assert sigma_k4 == gauss.calibrate_sigma_analytic(80.0, 1.0, 1e-5)
        assert sigma_k4 > sigma_k1

    def test_rejects_non_positive_max_contributions(self):
        with pytest.raises(ValueError, match="max_contributions"):
            gauss.private_sum_gaussian_analytic(100, 1.0, 1e-5, 0, 20, max_contributions=0)
