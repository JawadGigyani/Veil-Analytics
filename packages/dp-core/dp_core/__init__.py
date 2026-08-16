"""Veil dp-core: differential privacy mechanisms, sensitivity, and accounting."""

from dp_core.mechanisms.laplace import (
    BoundedMeanRelease,
    confidence_radius,
    laplace_noise,
    private_bounded_mean,
    private_bounded_mean_release,
    private_bounded_sum,
    private_count,
)
# Gaussian: gaussian_noise samples via Box-Muller over IEEE-754 floats and
# therefore carries the same Mironov (2012) low-order-bit exposure as
# laplace_noise; see gaussian.py's module docstring. There is no discrete
# Gaussian mechanism here to close it -- an honestly documented gap beats an
# unverified security mechanism. Prefer the *_analytic variants below
# (calibrate_sigma_analytic and the release functions built on it): they are
# valid for every epsilon > 0, unlike calibrate_sigma / private_count_gaussian
# / private_sum_gaussian, which implement the classical Dwork-Roth bound and
# are restricted to epsilon <= 1.
from dp_core.mechanisms.gaussian import (
    calibrate_sigma,
    calibrate_sigma_analytic,
    gaussian_confidence_radius,
    gaussian_noise,
    private_count_gaussian,
    private_count_gaussian_analytic,
    private_sum_gaussian,
    private_sum_gaussian_analytic,
)
from dp_core.mechanisms.discrete import (
    discrete_laplace_noise,
    private_count_discrete,
)
from dp_core.mechanisms.randomized_response import (
    aggregate_randomized_responses,
    estimate_frequencies,
    randomized_response,
)
from dp_core.mechanisms.noisy_max import (
    exponential_mechanism,
    noisy_argmax,
    private_top_k,
)
from dp_core.sensitivity.calculator import (
    bounded_mean_sensitivity,
    bounded_sum_sensitivity,
    count_sensitivity,
    histogram_sensitivity,
)
from dp_core.accounting.sequential import remaining_budget, sequential_composition
from dp_core.accounting.advanced import advanced_composition
from dp_core.accounting.gaussian_accounting import gaussian_composition

__all__ = [
    # Laplace
    "laplace_noise",
    "confidence_radius",
    "private_count",
    "private_bounded_sum",
    "private_bounded_mean",
    "private_bounded_mean_release",
    "BoundedMeanRelease",
    # Gaussian
    "gaussian_noise",
    "gaussian_confidence_radius",
    "calibrate_sigma",
    "calibrate_sigma_analytic",
    "private_count_gaussian",
    "private_sum_gaussian",
    "private_count_gaussian_analytic",
    "private_sum_gaussian_analytic",
    # Discrete Laplace (geometric mechanism)
    "discrete_laplace_noise",
    "private_count_discrete",
    # Randomized response
    "randomized_response",
    "aggregate_randomized_responses",
    "estimate_frequencies",
    # Noisy max / exponential mechanism
    "exponential_mechanism",
    "noisy_argmax",
    "private_top_k",
    # Sensitivity
    "count_sensitivity",
    "bounded_sum_sensitivity",
    "bounded_mean_sensitivity",
    "histogram_sensitivity",
    # Accounting
    "sequential_composition",
    "remaining_budget",
    "advanced_composition",
    "gaussian_composition",
]
