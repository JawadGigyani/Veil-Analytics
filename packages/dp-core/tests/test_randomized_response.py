"""Unit tests for randomized response."""

import pytest

from dp_core.mechanisms.randomized_response import (
    aggregate_randomized_responses,
    estimate_frequencies,
    randomized_response,
)

DOMAIN = ["a", "b", "c"]


class TestRandomizedResponse:
    def test_output_always_in_domain(self):
        for _ in range(100):
            result = randomized_response("a", DOMAIN, 1.0)
            assert result in DOMAIN

    def test_rejects_value_not_in_domain(self):
        with pytest.raises(ValueError, match="not in domain"):
            randomized_response("z", DOMAIN, 1.0)

    def test_rejects_non_positive_epsilon(self):
        with pytest.raises(ValueError):
            randomized_response("a", DOMAIN, 0)

    def test_rejects_empty_domain(self):
        with pytest.raises(ValueError):
            randomized_response("a", [], 1.0)

    def test_high_epsilon_mostly_truthful(self):
        truthful = sum(
            1 for _ in range(200)
            if randomized_response("a", DOMAIN, 10.0) == "a"
        )
        assert truthful > 150  # should be nearly all truthful


class TestAggregateRandomizedResponses:
    def test_returns_all_domain_keys(self):
        responses = ["a", "b", "a", "c"]
        result = aggregate_randomized_responses(responses, DOMAIN, 1.0)
        assert set(result.keys()) == set(DOMAIN)

    def test_estimated_counts_non_negative(self):
        responses = ["a"] * 50 + ["b"] * 30 + ["c"] * 20
        result = aggregate_randomized_responses(responses, DOMAIN, 1.0)
        for count in result.values():
            assert count >= 0

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            aggregate_randomized_responses([], [], 1.0)


class TestEstimateFrequencies:
    def test_frequencies_sum_to_one(self):
        responses = ["a"] * 40 + ["b"] * 35 + ["c"] * 25
        freqs = estimate_frequencies(responses, DOMAIN, 2.0)
        assert abs(sum(freqs.values()) - 1.0) < 1e-10

    def test_frequencies_in_zero_one(self):
        responses = ["a"] * 40 + ["b"] * 35 + ["c"] * 25
        freqs = estimate_frequencies(responses, DOMAIN, 2.0)
        for f in freqs.values():
            assert 0 <= f <= 1

    def test_rejects_zero_population(self):
        with pytest.raises(ValueError):
            estimate_frequencies([], DOMAIN, 1.0, n=0)
