"""Gaussian mechanism composition accounting.

Under zCDP or approximate-DP, Gaussian mechanisms compose more
naturally than Laplace: the variances sum.
"""

from __future__ import annotations

import math


def gaussian_composition(
    sigmas: list[float],
    delta: float,
) -> float:
    """Compute the composed epsilon for multiple Gaussian mechanisms.

    Each mechanism uses N(0, σᵢ²) noise.  Under Gaussian DP, the
    composed mechanism has σ_total² = 1 / Σ(1/σᵢ²) and the resulting
    (ε, δ)-DP guarantee is recovered via:
        ε = √(2 · ln(1.25/δ)) / σ_total

    This assumes each mechanism has sensitivity 1.

    This inverts the same classical Dwork–Roth bound used by
    :func:`dp_core.mechanisms.gaussian.calibrate_sigma`, so the result is only
    a valid (ε, δ)-DP guarantee when it lands at or below
    ``MAX_CLASSICAL_EPSILON``.  A larger return value means the composed
    mechanism is outside the range this bound can certify; it is reported for
    diagnostics and must not be treated as an enforced guarantee.
    """
    if not sigmas:
        return 0.0
    if delta <= 0 or delta >= 1:
        raise ValueError("Delta must be in (0, 1)")
    if any(s <= 0 for s in sigmas):
        raise ValueError("All sigmas must be positive")

    # Sum of reciprocal variances gives combined precision
    precision_sum = sum(1.0 / (s * s) for s in sigmas)
    sigma_combined = 1.0 / math.sqrt(precision_sum)

    return math.sqrt(2.0 * math.log(1.25 / delta)) / sigma_combined
