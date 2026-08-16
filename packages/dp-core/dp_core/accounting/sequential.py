"""Basic sequential composition for ε-differential privacy.

Under sequential composition the total privacy loss is the sum of
individual epsilons.
"""

from __future__ import annotations


def sequential_composition(epsilons: list[float]) -> float:
    """Total epsilon under basic sequential composition."""
    return sum(epsilons)


def remaining_budget(
    epsilon_total: float,
    spent: list[float],
) -> float:
    """Remaining epsilon budget after spent queries.

    Returns 0 if the budget is exhausted (never negative).
    """
    return max(0.0, epsilon_total - sequential_composition(spent))
