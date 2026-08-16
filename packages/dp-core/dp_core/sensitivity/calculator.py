"""Sensitivity calculator for supported aggregate queries.

All functions assume add/remove-one-ENTITY adjacency, where an entity (a
person, or whatever unit the privacy policy declares) may contribute up to
``max_contributions`` rows to the dataset.  Add/remove-one-ROW adjacency is
the special case ``max_contributions=1``, which is also every function's
default so existing callers that never heard of entities are unaffected.

Getting this wrong is not a rounding error: releasing a query calibrated for
sensitivity 1 against a dataset where one entity can contribute k rows
understates the true sensitivity by a factor of k, and every epsilon spent
against that release is wrong by the same factor.
"""

from __future__ import annotations


def _validate_max_contributions(max_contributions: int) -> None:
    if max_contributions < 1:
        raise ValueError("max_contributions must be >= 1")


def count_sensitivity(max_contributions: int = 1) -> int:
    """Global sensitivity for a count query.

    Adding or removing one entity changes the count by at most
    ``max_contributions``, the most rows that single entity may contribute.
    """
    _validate_max_contributions(max_contributions)
    return max_contributions


def histogram_sensitivity(max_contributions: int = 1) -> int:
    """Global sensitivity for a histogram bin count.

    Adding or removing one entity changes at most one bin's count, by at
    most ``max_contributions`` -- all of that entity's rows could fall in
    the same bin.
    """
    _validate_max_contributions(max_contributions)
    return max_contributions


def bounded_sum_sensitivity(
    lower: float,
    upper: float,
    max_contributions: int = 1,
) -> float:
    """Global sensitivity for a bounded sum query.

    Each value is clipped to [lower, upper], so one row moves the sum by at
    most max(|lower|, |upper|).  One entity may contribute up to
    ``max_contributions`` such rows, so removing that entity moves the sum
    by at most ``max_contributions * max(|lower|, |upper|)``.
    """
    if lower >= upper:
        raise ValueError("Lower bound must be less than upper bound")
    _validate_max_contributions(max_contributions)
    return max_contributions * max(abs(lower), abs(upper))


def bounded_mean_sensitivity(
    lower: float,
    upper: float,
    n: int,
) -> float:
    """Approximate global sensitivity for a bounded mean.

    For a bounded mean computed as (clipped_sum / count) the sensitivity
    depends on the dataset size.  This returns (upper - lower) / n which
    is the sensitivity of the sum component when counts are public.

    For the private-mean mechanism that uses a split-epsilon noisy sum and
    noisy count, the sensitivities of the two components are handled
    separately inside the mechanism itself -- see the executor's
    entity-bounded release path for the max_contributions-scaled form of
    each component (shifted-sum sensitivity max_contributions * (upper -
    lower), count sensitivity max_contributions).
    """
    if lower >= upper:
        raise ValueError("Lower bound must be less than upper bound")
    if n <= 0:
        raise ValueError("Population size must be positive")
    return (upper - lower) / n


def grouped_count_sensitivity(max_contributions: int = 1) -> int:
    """Sensitivity for a grouped count.

    Same as count sensitivity: removing one entity changes at most one
    group's count, by at most ``max_contributions`` (all of that entity's
    rows could fall in the same group).
    """
    _validate_max_contributions(max_contributions)
    return max_contributions
