"""Thin privacy facade for the analytics worker.

Delegates to ``dp_core`` for all mechanism implementations.  This module
exists to preserve import compatibility with existing worker code and
tests that monkeypatch ``privacy.laplace``.
"""

from __future__ import annotations

import math
import secrets

# Re-export from dp-core for backward compatibility
from dp_core.mechanisms.laplace import (
    BoundedMeanRelease,
    laplace_noise,
    private_bounded_mean,
    private_bounded_mean_release,
    private_bounded_sum,
    private_count,
    confidence_radius,
)
from dp_core.mechanisms.discrete import (
    discrete_laplace_noise,
    private_count_discrete,
)
from dp_core.mechanisms.gaussian import (
    calibrate_sigma,
    calibrate_sigma_analytic,
    gaussian_noise,
    private_count_gaussian,
    private_count_gaussian_analytic,
    private_sum_gaussian,
    private_sum_gaussian_analytic,
    gaussian_confidence_radius,
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


def laplace(scale: float) -> float:
    """Backward-compatible wrapper for laplace_noise.

    Tests may monkeypatch this function to inject deterministic noise.
    """
    return laplace_noise(scale)


def discrete_laplace(scale: float) -> int:
    """Wrapper for discrete_laplace_noise.

    Counts, grouped counts, and histogram buckets are integer-valued, so they
    use the discrete (geometric) mechanism rather than the floating-point
    inverse-CDF path.  Float Laplace sampling is attackable through the
    low-order bits of the released value (Mironov, 2012); the discrete sampler
    has no float rounding step for that attack to exploit.

    Tests may monkeypatch this function to inject deterministic noise.
    """
    return discrete_laplace_noise(scale)
