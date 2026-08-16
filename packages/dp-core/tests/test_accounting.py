"""Unit tests for privacy accounting."""

import math

import pytest

from dp_core.accounting.advanced import advanced_composition
from dp_core.accounting.gaussian_accounting import gaussian_composition
from dp_core.accounting.sequential import remaining_budget, sequential_composition


class TestSequentialComposition:
    def test_sums_epsilons(self):
        assert sequential_composition([0.5, 1.0, 0.3]) == pytest.approx(1.8)

    def test_empty_is_zero(self):
        assert sequential_composition([]) == 0.0


class TestRemainingBudget:
    def test_basic(self):
        assert remaining_budget(5.0, [1.0, 0.5]) == pytest.approx(3.5)

    def test_exhausted(self):
        assert remaining_budget(2.0, [1.0, 1.5]) == 0.0

    def test_overspent_clamps_to_zero(self):
        assert remaining_budget(1.0, [0.8, 0.5]) == 0.0


class TestAdvancedComposition:
    def test_empty_is_zero(self):
        assert advanced_composition([]) == 0.0

    def test_tighter_than_sequential(self):
        # Advanced composition is tighter for many small-epsilon queries
        epsilons = [0.1] * 100
        adv = advanced_composition(epsilons, 1e-6)
        seq = sequential_composition(epsilons)
        assert adv < seq

    def test_single_query_close_to_sequential(self):
        adv = advanced_composition([1.0], 1e-6)
        seq = sequential_composition([1.0])
        # With one query, advanced should be close to or slightly above sequential
        assert adv >= seq * 0.9

    def test_rejects_invalid_delta(self):
        with pytest.raises(ValueError):
            advanced_composition([1.0], 0)


class TestGaussianComposition:
    def test_empty_is_zero(self):
        assert gaussian_composition([], 1e-5) == 0.0

    def test_single_sigma(self):
        sigma = 2.0
        eps = gaussian_composition([sigma], 1e-5)
        expected = math.sqrt(2 * math.log(1.25 / 1e-5)) / sigma
        assert eps == pytest.approx(expected)

    def test_more_queries_increases_epsilon(self):
        eps1 = gaussian_composition([2.0], 1e-5)
        eps2 = gaussian_composition([2.0, 2.0], 1e-5)
        assert eps2 > eps1

    def test_rejects_non_positive_sigma(self):
        with pytest.raises(ValueError):
            gaussian_composition([0], 1e-5)

    def test_rejects_invalid_delta(self):
        with pytest.raises(ValueError):
            gaussian_composition([1.0], 0)
