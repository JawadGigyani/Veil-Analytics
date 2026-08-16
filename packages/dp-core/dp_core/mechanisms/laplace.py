"""Laplace mechanism for (ε)-differential privacy.

Provides calibrated Laplace noise for counts, bounded sums, and bounded means.
All randomness uses ``secrets.randbits`` so that noise is not predictable.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass


def laplace_noise(scale: float) -> float:
    """Sample from Laplace(0, scale) using cryptographic randomness.

    The inverse-CDF transform is guarded against log(0) at the random
    generator endpoints.
    """
    if scale <= 0:
        raise ValueError("Scale must be positive")
    u = secrets.randbits(53) / (1 << 53) - 0.5
    tail = max(math.nextafter(0.0, 1.0), 1 - 2 * abs(u))
    return -scale * math.copysign(1, u or 1e-12) * math.log(tail)


def private_count(value: int, epsilon: float) -> int:
    """Release a differentially private count with Laplace noise.

    Sensitivity is 1 for add/remove-one adjacency.
    The result is clamped to be non-negative.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    return max(0, round(value + laplace_noise(1 / epsilon)))


def private_bounded_sum(
    value: float,
    epsilon: float,
    lower: float,
    upper: float,
    max_contributions: int = 1,
) -> float:
    """Release a differentially private bounded sum.

    Each record is assumed to be clipped to [lower, upper] before summing.
    Sensitivity is ``max_contributions * max(|lower|, |upper|)``: removing an
    entity that contributed up to k clipped rows moves the sum by up to that
    much.  The default of 1 is the add/remove-one-row special case.
    """
    if epsilon <= 0 or lower >= upper:
        raise ValueError("Positive epsilon and valid public bounds are required")
    if max_contributions < 1:
        raise ValueError("max_contributions must be at least 1")
    sensitivity = max_contributions * max(abs(lower), abs(upper))
    return value + laplace_noise(sensitivity / epsilon)


@dataclass(frozen=True)
class BoundedMeanRelease:
    """A released bounded mean together with the figures needed to explain it.

    ``denominator`` is the divisor the mechanism actually used -- the noisy
    count floored at the public minimum.  A caller cannot reconstruct it, and
    without it no honest uncertainty figure can be reported, because the error
    in the released mean scales as 1 / denominator.
    """

    value: float
    denominator: float
    confidence_radius: float


def private_bounded_mean_release(
    total: float,
    count: int,
    epsilon: float,
    lower: float,
    upper: float,
    minimum_denominator: int,
    max_contributions: int = 1,
) -> BoundedMeanRelease:
    """Release a differentially private bounded mean with its uncertainty.

    Uses a split-epsilon approach: half the budget for a noisy shifted sum,
    half for a noisy count.  The denominator is floored at
    ``minimum_denominator`` to avoid division by near-zero.  The result is
    clamped to [lower, upper].

    The reported ``confidence_radius`` covers the **sum-noise term only**:

        ln(20) * (upper - lower) / ((epsilon / 2) * denominator)

    The independent noise on the count contributes a further error term that
    this figure does not include, and the post-processing clamp to
    [lower, upper] truncates the tails.  It is therefore an indicative scale
    for the noise, not a calibrated 95% confidence interval, and must not be
    presented as one.
    """
    if epsilon <= 0 or lower >= upper or minimum_denominator <= 0:
        raise ValueError("Valid public mean parameters are required")
    if max_contributions < 1:
        raise ValueError("max_contributions must be at least 1")
    half_epsilon = epsilon / 2
    shifted_sum = total - lower * count
    # Both halves scale with the contribution bound: removing an entity that
    # contributed up to k rows moves the shifted sum by up to k*(upper-lower)
    # and the count by up to k.
    noisy_sum = shifted_sum + laplace_noise(
        max_contributions * (upper - lower) / half_epsilon
    )
    noisy_count = count + laplace_noise(max_contributions / half_epsilon)
    denominator = max(float(minimum_denominator), noisy_count)
    raw = round(lower + noisy_sum / denominator, 2)
    # float() keeps the return type stable: min/max return whichever argument
    # compared smaller, so an exact tie against an int bound would otherwise
    # make the return type depend on the noise draw.
    value = float(min(upper, max(lower, raw)))
    radius = confidence_radius(half_epsilon, max_contributions * (upper - lower)) / denominator
    return BoundedMeanRelease(
        value=value,
        denominator=round(denominator, 2),
        confidence_radius=round(radius, 2),
    )


def private_bounded_mean(
    total: float,
    count: int,
    epsilon: float,
    lower: float,
    upper: float,
    minimum_denominator: int,
    max_contributions: int = 1,
) -> float:
    """Release a differentially private bounded mean.

    Thin wrapper over :func:`private_bounded_mean_release` for callers that
    only need the released value.  Prefer the release form when the result is
    shown to a person, so the uncertainty can be shown alongside it.
    """
    return private_bounded_mean_release(
        total, count, epsilon, lower, upper, minimum_denominator, max_contributions,
    ).value


def confidence_radius(epsilon: float, sensitivity: float = 1.0) -> float:
    """Approximate 95% confidence radius for Laplace noise.

    For Laplace(0, b), the 95th-percentile absolute deviation is
    b * ln(20) ≈ b * 2.9957.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    return sensitivity / epsilon * math.log(20)
