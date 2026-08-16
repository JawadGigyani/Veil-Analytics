"""Hypothesis property-based tests for dp-core mechanisms.

These tests verify invariants that must hold for any valid input.
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from dp_core.mechanisms.laplace import (
    laplace_noise,
    private_bounded_mean,
    private_bounded_sum,
    private_count,
)
from dp_core.mechanisms.gaussian import (
    calibrate_sigma,
    gaussian_noise,
    private_count_gaussian,
    private_sum_gaussian,
)
from dp_core.mechanisms.randomized_response import randomized_response
from dp_core.mechanisms.noisy_max import exponential_mechanism, noisy_argmax
from dp_core.accounting.sequential import remaining_budget, sequential_composition


# -- Laplace properties --

@given(epsilon=st.floats(min_value=0.01, max_value=10.0))
@settings(max_examples=100)
def test_private_count_is_never_negative(epsilon):
    result = private_count(5, epsilon)
    assert result >= 0


@given(epsilon=st.floats(min_value=0.01, max_value=10.0))
@settings(max_examples=100)
def test_private_count_zero_input_is_non_negative(epsilon):
    result = private_count(0, epsilon)
    assert result >= 0


@given(
    total=st.floats(min_value=-1000, max_value=1000),
    count=st.integers(min_value=1, max_value=500),
    epsilon=st.floats(min_value=0.01, max_value=10.0),
    lower=st.floats(min_value=-100, max_value=0),
    upper=st.floats(min_value=1, max_value=100),
)
@settings(max_examples=80)
def test_bounded_mean_within_bounds(total, count, epsilon, lower, upper):
    assume(lower < upper)
    result = private_bounded_mean(total, count, epsilon, lower, upper, max(1, count))
    assert lower <= result <= upper


@given(
    value=st.floats(min_value=-500, max_value=500),
    epsilon=st.floats(min_value=0.01, max_value=10.0),
    lower=st.floats(min_value=-100, max_value=0),
    upper=st.floats(min_value=1, max_value=100),
)
@settings(max_examples=60)
def test_bounded_sum_returns_finite(value, epsilon, lower, upper):
    assume(lower < upper)
    import math
    result = private_bounded_sum(value, epsilon, lower, upper)
    assert math.isfinite(result)


# -- Gaussian properties --

@given(
    sensitivity=st.floats(min_value=0.01, max_value=100),
    # The classical bound is only proven for epsilon <= 1; calibrate_sigma
    # rejects anything above that rather than mis-calibrating.
    epsilon=st.floats(min_value=0.01, max_value=1.0),
    delta=st.floats(min_value=1e-10, max_value=0.1),
)
@settings(max_examples=80)
def test_gaussian_sigma_is_positive(sensitivity, epsilon, delta):
    sigma = calibrate_sigma(sensitivity, epsilon, delta)
    assert sigma > 0


@given(
    sensitivity=st.floats(min_value=0.01, max_value=100),
    epsilon=st.floats(min_value=1.0 + 1e-9, max_value=50),
    delta=st.floats(min_value=1e-10, max_value=0.1),
)
@settings(max_examples=50)
def test_gaussian_sigma_rejects_epsilon_above_one(sensitivity, epsilon, delta):
    with pytest.raises(ValueError, match="only valid for epsilon"):
        calibrate_sigma(sensitivity, epsilon, delta)


@given(epsilon=st.floats(min_value=0.1, max_value=1.0))
@settings(max_examples=50)
def test_gaussian_count_is_non_negative(epsilon):
    result = private_count_gaussian(10, epsilon, 1e-5)
    assert result >= 0


@given(sigma=st.floats(min_value=0.01, max_value=50.0))
@settings(max_examples=50)
def test_gaussian_noise_is_finite(sigma):
    import math
    result = gaussian_noise(sigma)
    assert math.isfinite(result)


# -- Randomized response properties --

@given(
    epsilon=st.floats(min_value=0.01, max_value=10.0),
    idx=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=100)
def test_randomized_response_output_in_domain(epsilon, idx):
    domain = ["a", "b", "c", "d", "e"]
    true_value = domain[idx]
    result = randomized_response(true_value, domain, epsilon)
    assert result in domain


# -- Exponential mechanism properties --

@given(
    epsilon=st.floats(min_value=0.01, max_value=10.0),
    n=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=80)
def test_exponential_mechanism_valid_index(epsilon, n):
    scores = [float(i) for i in range(n)]
    idx = exponential_mechanism(scores, 1.0, epsilon)
    assert 0 <= idx < n


@given(
    epsilon=st.floats(min_value=0.01, max_value=10.0),
    n=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=80)
def test_noisy_argmax_valid_index(epsilon, n):
    counts = [float(i * 10) for i in range(n)]
    idx = noisy_argmax(counts, epsilon)
    assert 0 <= idx < n


# -- Accounting properties --

@given(
    epsilons=st.lists(st.floats(min_value=0.01, max_value=2.0), min_size=0, max_size=20),
)
@settings(max_examples=80)
def test_sequential_composition_is_non_negative(epsilons):
    assert sequential_composition(epsilons) >= 0


@given(
    total=st.floats(min_value=0.1, max_value=100),
    epsilons=st.lists(st.floats(min_value=0.01, max_value=2.0), min_size=0, max_size=20),
)
@settings(max_examples=80)
def test_remaining_budget_is_non_negative(total, epsilons):
    assert remaining_budget(total, epsilons) >= 0
