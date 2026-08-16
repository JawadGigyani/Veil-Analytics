"""Gaussian mechanism for (ε, δ)-differential privacy.

The Gaussian mechanism adds N(0, σ²) noise where σ is calibrated so that
the mechanism satisfies (ε, δ)-DP.  It is preferred over Laplace when the
analyst can tolerate a small failure probability δ in exchange for lower
noise variance at the same ε.

**Residual noise-sampling limitation.**  ``gaussian_noise`` below samples via
Box-Muller over IEEE-754 floats.  Exactly like ``laplace.laplace_noise``, this
is attackable through the low-order bits of the *released floating-point
value* (Mironov, 2012, "On significance of the least significant bits for
differential privacy") -- the achieved output distribution is a rounded
approximation of the textbook Gaussian whose per-value probabilities depend
on local float density, not the exact distribution the epsilon/delta
accounting assumes. Counts and histogram buckets released through the
Gaussian mechanism round and clamp the noised float to an integer
(:func:`private_count_gaussian_analytic`), which narrows but does not close
the gap the way the *discrete* Gaussian mechanism would; real-valued releases
(:func:`private_sum_gaussian_analytic`) remain fully exposed to it, the same
as ``dp_core.mechanisms.laplace``'s real-valued paths.  A discrete Gaussian
sampler (Canonne-Kamath-Steinke 2020, the same paper
``dp_core.mechanisms.discrete`` implements for Laplace) would close this, but
it is **not implemented here**: getting its rejection sampling exactly right
is easy to get subtly wrong, and shipping an unverified version of a security
mechanism is worse than clearly declaring the gap. See
``dp_core/mechanisms/discrete.py``'s module docstring for the full attack
description and the same judgment call applied to Laplace.
"""

from __future__ import annotations

import math
import secrets


def _uniform() -> float:
    """Uniform(0, 1) from cryptographic bits, excluding exact 0."""
    u = secrets.randbits(53) / (1 << 53)
    return max(math.nextafter(0.0, 1.0), u)


def gaussian_noise(sigma: float) -> float:
    """Sample from N(0, σ²) using Box-Muller with cryptographic randomness."""
    if sigma <= 0:
        raise ValueError("Sigma must be positive")
    u1 = _uniform()
    u2 = _uniform()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
    return sigma * z


#: The classical Gaussian bound below is only proven for ε ≤ 1.  Calibrating
#: with it at larger ε produces a σ that does not satisfy (ε, δ)-DP.
MAX_CLASSICAL_EPSILON = 1.0


def calibrate_sigma(
    sensitivity: float,
    epsilon: float,
    delta: float,
) -> float:
    """Compute σ for (ε, δ)-DP using the classical Gaussian mechanism bound.

    σ = sensitivity × √(2 ln(1.25 / δ)) / ε

    This is the Dwork–Roth bound (Appendix A), which is **only valid for
    ε ≤ 1**.  Above that it understates the required noise and the release is
    not (ε, δ)-differentially private, so ε > 1 is rejected rather than
    silently mis-calibrated.  Removing that restriction requires the analytic
    Gaussian mechanism of Balle and Wang (2018), which is not implemented here.

    Requires 0 < ε ≤ 1 and 0 < δ < 1.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if epsilon > MAX_CLASSICAL_EPSILON:
        raise ValueError(
            f"The classical Gaussian bound is only valid for epsilon <= "
            f"{MAX_CLASSICAL_EPSILON}; got {epsilon}. Use the Laplace "
            f"mechanism, or implement analytic (Balle-Wang) calibration."
        )
    if delta <= 0 or delta >= 1:
        raise ValueError("Delta must be in the open interval (0, 1)")
    if sensitivity <= 0:
        raise ValueError("Sensitivity must be positive")
    return sensitivity * math.sqrt(2.0 * math.log(1.25 / delta)) / epsilon


def _gaussian_privacy_profile_delta(
    sigma: float,
    sensitivity: float,
    epsilon: float,
) -> float:
    """Evaluate the exact (ε, δ) privacy profile of N(0, σ²) at a given σ.

    delta(eps) = Phi(Δ/(2σ) - εσ/Δ) - exp(ε) * Phi(-Δ/(2σ) - εσ/Δ)

    This is the tight, exact expression for the δ achieved by the Gaussian
    mechanism at a fixed ε and σ (Balle & Wang 2018, Theorem 8), valid for
    every ε > 0 -- unlike the classical bound, it is not an approximation
    that only holds below some cutoff.  It is monotonically decreasing in σ
    (more noise -> smaller δ for the same ε), which is what makes the binary
    search in ``calibrate_sigma_analytic`` well posed.
    """
    from scipy.stats import norm  # type: ignore[import-untyped]

    a = sensitivity / (2 * sigma) - epsilon * sigma / sensitivity
    b = -sensitivity / (2 * sigma) - epsilon * sigma / sensitivity
    return float(norm.cdf(a) - math.exp(epsilon) * norm.cdf(b))


def calibrate_sigma_analytic(
    sensitivity: float,
    epsilon: float,
    delta: float,
) -> float:
    """Compute σ for (ε, δ)-DP using analytic Gaussian calibration.

    Implements Balle and Wang, "Improving the Gaussian Mechanism for
    Differential Privacy: Analytical Calibration and Optimal Denoising"
    (2018).  Instead of inverting a closed-form bound that is only proven for
    ε ≤ 1 (:func:`calibrate_sigma`), this binary-searches directly on the
    *exact* Gaussian privacy profile

        δ(ε) = Φ(Δ/(2σ) - εσ/Δ) - exp(ε)·Φ(-Δ/(2σ) - εσ/Δ)

    for the smallest σ with δ(ε) ≤ the requested δ.  Because the profile is
    exact rather than a bound, this is valid for **every** ε > 0, and for
    ε ≤ 1 it never returns a larger σ than the classical bound -- it is at
    least as tight, and strictly tighter away from the edge cases.

    Requires ε > 0 and 0 < δ < 1.  Prefer this over :func:`calibrate_sigma`
    for any ε > 1; either is valid at ε ≤ 1, and this one is never worse.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if delta <= 0 or delta >= 1:
        raise ValueError("Delta must be in the open interval (0, 1)")
    if sensitivity <= 0:
        raise ValueError("Sensitivity must be positive")

    def delta_at(sigma: float) -> float:
        return _gaussian_privacy_profile_delta(sigma, sensitivity, epsilon)

    # delta_at(sigma) falls monotonically from ~1 (sigma -> 0) to 0
    # (sigma -> infinity), so bisection on sigma has a unique crossing point.
    sigma_lo = 1e-12 * sensitivity  # delta_at here is ~1, above any valid target
    sigma_hi = sensitivity if sensitivity > 0 else 1.0
    doublings = 0
    while delta_at(sigma_hi) > delta:
        sigma_hi *= 2
        doublings += 1
        if doublings > 200:
            raise RuntimeError(
                "Failed to bracket sigma for analytic Gaussian calibration"
            )

    # 100 bisection steps narrows the bracket by a factor of 2^100, far past
    # double precision -- the loop is cheap (norm.cdf calls only) so there is
    # no reason to cut it short.
    for _ in range(100):
        mid = (sigma_lo + sigma_hi) / 2
        if delta_at(mid) > delta:
            sigma_lo = mid
        else:
            sigma_hi = mid
    return sigma_hi


def private_count_gaussian(
    value: int,
    epsilon: float,
    delta: float,
) -> int:
    """Release a differentially private count using the Gaussian mechanism.

    Sensitivity is 1.  The result is clamped to be non-negative.

    Calibrates with :func:`calibrate_sigma` (the classical bound), so this
    is restricted to ε ≤ 1 exactly like that function.  Prefer
    :func:`private_count_gaussian_analytic` for ε > 1.
    """
    sigma = calibrate_sigma(1.0, epsilon, delta)
    return max(0, round(value + gaussian_noise(sigma)))


def private_sum_gaussian(
    value: float,
    epsilon: float,
    delta: float,
    lower: float,
    upper: float,
) -> float:
    """Release a differentially private bounded sum using the Gaussian mechanism.

    Each record is assumed to be clipped to [lower, upper].
    Sensitivity is max(|lower|, |upper|).

    Calibrates with :func:`calibrate_sigma` (the classical bound), so this
    is restricted to ε ≤ 1 exactly like that function.  Prefer
    :func:`private_sum_gaussian_analytic` for ε > 1.
    """
    if lower >= upper:
        raise ValueError("Valid public bounds are required")
    sensitivity = max(abs(lower), abs(upper))
    sigma = calibrate_sigma(sensitivity, epsilon, delta)
    return value + gaussian_noise(sigma)


def private_count_gaussian_analytic(
    value: int,
    epsilon: float,
    delta: float,
    max_contributions: int = 1,
) -> tuple[int, float]:
    """Release a differentially private count via analytically-calibrated Gaussian noise.

    Unlike :func:`private_count_gaussian`, this calibrates with
    :func:`calibrate_sigma_analytic` rather than the classical bound, so it
    is valid for **every** ε > 0 -- there is no ε ≤ 1 restriction to inherit.
    This is the function the analytics worker's release path uses.

    Sensitivity is ``max_contributions``: under add/remove-one-entity
    adjacency, removing an entity that contributed up to k rows moves the
    count by up to k.  The default of 1 is the add/remove-one-row special
    case, matching every other mechanism in this package.

    The result is clamped to be non-negative, same as the other count
    mechanisms.

    Returns:
        A ``(released_value, sigma)`` tuple.  ``sigma`` is returned alongside
        the value -- not just logged internally -- so a caller can report
        the exact noise scale a release used, the way ``BoundedMeanRelease``
        already does for the bounded mean: a released figure without the
        parameter that calibrated it cannot be explained to the analyst who
        receives it.
    """
    if max_contributions < 1:
        raise ValueError("max_contributions must be at least 1")
    sigma = calibrate_sigma_analytic(float(max_contributions), epsilon, delta)
    return max(0, round(value + gaussian_noise(sigma))), sigma


def private_sum_gaussian_analytic(
    value: float,
    epsilon: float,
    delta: float,
    lower: float,
    upper: float,
    max_contributions: int = 1,
) -> tuple[float, float]:
    """Release a differentially private bounded sum via analytically-calibrated Gaussian noise.

    Unlike :func:`private_sum_gaussian`, this calibrates with
    :func:`calibrate_sigma_analytic` rather than the classical bound, so it
    is valid for **every** ε > 0.  This is the function the analytics
    worker's release path uses.

    Each record is assumed to be clipped to [lower, upper] before summing.
    Sensitivity is ``max_contributions * max(|lower|, |upper|)``, matching
    ``laplace.private_bounded_sum``'s sensitivity under the same contribution
    bound.

    Returns:
        A ``(released_value, sigma)`` tuple -- see
        :func:`private_count_gaussian_analytic` for why sigma is returned
        rather than only used internally.
    """
    if lower >= upper:
        raise ValueError("Valid public bounds are required")
    if max_contributions < 1:
        raise ValueError("max_contributions must be at least 1")
    sensitivity = max_contributions * max(abs(lower), abs(upper))
    sigma = calibrate_sigma_analytic(sensitivity, epsilon, delta)
    return value + gaussian_noise(sigma), sigma


def gaussian_confidence_radius(
    sigma: float,
    confidence: float = 0.95,
) -> float:
    """Return the confidence radius for Gaussian noise at the given level.

    For N(0, σ²), the symmetric interval containing ``confidence`` fraction
    of the distribution is ±σ × Φ⁻¹((1 + confidence) / 2).
    """
    from scipy.stats import norm  # type: ignore[import-untyped]

    return sigma * norm.ppf((1 + confidence) / 2)
