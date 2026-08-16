"""Unit tests for exponential mechanism and noisy-max."""

import pytest

from dp_core.mechanisms.noisy_max import (
    exponential_mechanism,
    noisy_argmax,
    private_top_k,
)


class TestExponentialMechanism:
    def test_returns_valid_index(self):
        for _ in range(50):
            idx = exponential_mechanism([1.0, 2.0, 3.0], 1.0, 1.0)
            assert 0 <= idx <= 2

    def test_rejects_empty_scores(self):
        with pytest.raises(ValueError):
            exponential_mechanism([], 1.0, 1.0)

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            exponential_mechanism([1.0], 1.0, 0)

    def test_rejects_non_positive_sensitivity(self):
        with pytest.raises(ValueError):
            exponential_mechanism([1.0], 0, 1.0)

    def test_high_epsilon_selects_best(self):
        scores = [1.0, 100.0, 2.0]
        best_count = sum(
            1 for _ in range(100)
            if exponential_mechanism(scores, 1.0, 50.0) == 1
        )
        assert best_count > 90

    def test_single_element(self):
        assert exponential_mechanism([5.0], 1.0, 1.0) == 0


class TestNoisyArgmax:
    def test_returns_valid_index(self):
        for _ in range(50):
            idx = noisy_argmax([10, 5, 1], 1.0)
            assert 0 <= idx <= 2

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            noisy_argmax([], 1.0)

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            noisy_argmax([1], 0)

    def test_tends_to_select_highest(self):
        counts = [100, 5, 3]
        selected = [noisy_argmax(counts, 2.0) for _ in range(100)]
        assert selected.count(0) > 70


class TestPrivateTopK:
    def test_returns_at_most_k(self):
        counts = [50, 30, 20, 10, 5]
        labels = ["a", "b", "c", "d", "e"]
        result = private_top_k(counts, labels, 3, 1.0)
        assert len(result) <= 3

    def test_each_result_has_required_keys(self):
        result = private_top_k([10, 20], ["x", "y"], 2, 1.0)
        for item in result:
            assert "group" in item
            assert "value" in item
            assert "range" in item

    def test_values_are_non_negative(self):
        result = private_top_k([50, 30, 20], ["a", "b", "c"], 3, 1.0)
        for item in result:
            assert item["value"] >= 0  # type: ignore[operator]

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError):
            private_top_k([1, 2], ["a"], 1, 1.0)

    def test_empty_input(self):
        assert private_top_k([], [], 3, 1.0) == []

    def test_k_larger_than_candidates(self):
        result = private_top_k([10, 5], ["a", "b"], 10, 1.0)
        assert len(result) <= 2

    def test_sorted_descending(self):
        result = private_top_k([100, 50, 10], ["a", "b", "c"], 3, 2.0)
        values = [r["value"] for r in result]
        assert values == sorted(values, reverse=True)
