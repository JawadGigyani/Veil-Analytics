"""Advanced (strong) composition theorem.

The advanced composition theorem gives a tighter bound than basic
sequential composition when many queries are answered.
"""

from __future__ import annotations

import math


def advanced_composition(
    epsilons: list[float],
    delta_prime: float = 1e-6,
) -> float:
    """Upper bound on total epsilon under advanced composition.

    Uses the strong composition theorem:
        ε_total ≤ √(2 · ln(1/δ') · Σεᵢ²) + Σ εᵢ(e^εᵢ - 1)

    Args:
        epsilons: Per-query epsilon values.
        delta_prime: Failure probability for the composition bound.

    Returns:
        The composed epsilon bound.
    """
    if not epsilons:
        return 0.0
    if delta_prime <= 0 or delta_prime >= 1:
        raise ValueError("delta_prime must be in (0, 1)")

    sum_squares = sum(e * e for e in epsilons)
    correction = sum(e * math.expm1(e) for e in epsilons)
    return math.sqrt(2.0 * math.log(1.0 / delta_prime) * sum_squares) + correction
