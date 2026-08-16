"""Unit tests for sensitivity calculator."""

import pytest

from dp_core.sensitivity.calculator import (
    bounded_mean_sensitivity,
    bounded_sum_sensitivity,
    count_sensitivity,
    grouped_count_sensitivity,
    histogram_sensitivity,
)


def test_count_sensitivity_is_one():
    assert count_sensitivity() == 1


def test_histogram_sensitivity_is_one():
    assert histogram_sensitivity() == 1


def test_grouped_count_sensitivity_is_one():
    assert grouped_count_sensitivity() == 1


class TestSensitivityScalesWithMaxContributions:
    """Under add/remove-one-ENTITY adjacency, sensitivity scales linearly
    with the number of rows a single entity may contribute."""

    @pytest.mark.parametrize("k", [1, 2, 5, 10, 100])
    def test_count_sensitivity_equals_k(self, k):
        assert count_sensitivity(k) == k

    @pytest.mark.parametrize("k", [1, 2, 5, 10, 100])
    def test_histogram_sensitivity_equals_k(self, k):
        assert histogram_sensitivity(k) == k

    @pytest.mark.parametrize("k", [1, 2, 5, 10, 100])
    def test_grouped_count_sensitivity_equals_k(self, k):
        assert grouped_count_sensitivity(k) == k

    @pytest.mark.parametrize("k", [1, 2, 5, 10, 100])
    def test_bounded_sum_sensitivity_scales_linearly(self, k):
        base = bounded_sum_sensitivity(0, 100)
        assert bounded_sum_sensitivity(0, 100, k) == base * k

    def test_count_sensitivity_rejects_zero_or_negative_k(self):
        with pytest.raises(ValueError):
            count_sensitivity(0)
        with pytest.raises(ValueError):
            count_sensitivity(-1)

    def test_histogram_sensitivity_rejects_zero_or_negative_k(self):
        with pytest.raises(ValueError):
            histogram_sensitivity(0)

    def test_grouped_count_sensitivity_rejects_zero_or_negative_k(self):
        with pytest.raises(ValueError):
            grouped_count_sensitivity(0)

    def test_bounded_sum_sensitivity_rejects_zero_or_negative_k(self):
        with pytest.raises(ValueError):
            bounded_sum_sensitivity(0, 100, 0)
        with pytest.raises(ValueError):
            bounded_sum_sensitivity(0, 100, -3)


class TestBoundedSumSensitivity:
    def test_positive_bounds(self):
        assert bounded_sum_sensitivity(0, 100) == 100

    def test_negative_lower(self):
        assert bounded_sum_sensitivity(-50, 100) == 100

    def test_symmetric_bounds(self):
        assert bounded_sum_sensitivity(-30, 30) == 30

    def test_negative_range(self):
        assert bounded_sum_sensitivity(-100, -10) == 100

    def test_rejects_equal_bounds(self):
        with pytest.raises(ValueError):
            bounded_sum_sensitivity(5, 5)


class TestBoundedMeanSensitivity:
    def test_basic(self):
        assert bounded_mean_sensitivity(0, 100, 10) == 10.0

    def test_larger_population_lowers_sensitivity(self):
        s1 = bounded_mean_sensitivity(0, 100, 10)
        s2 = bounded_mean_sensitivity(0, 100, 100)
        assert s2 < s1

    def test_rejects_zero_population(self):
        with pytest.raises(ValueError):
            bounded_mean_sensitivity(0, 100, 0)

    def test_rejects_equal_bounds(self):
        with pytest.raises(ValueError):
            bounded_mean_sensitivity(5, 5, 10)
