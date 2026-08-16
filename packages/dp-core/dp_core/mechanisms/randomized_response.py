"""Randomized response for categorical and binary data.

Each respondent reports their true value with probability p = e^ε / (e^ε + |D| - 1)
and a uniformly random value from the domain D otherwise.  This provides ε-DP for
each individual response.  Aggregated responses can be de-biased to estimate true
category frequencies.
"""

from __future__ import annotations

import math
import secrets


def randomized_response(
    true_value: str,
    domain: list[str],
    epsilon: float,
) -> str:
    """Apply randomized response to a single categorical value.

    Args:
        true_value: The individual's true value (must be in ``domain``).
        domain: The complete list of possible values.
        epsilon: Privacy parameter.

    Returns:
        Either the true value or a uniformly random domain element.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if not domain:
        raise ValueError("Domain must be non-empty")
    if true_value not in domain:
        raise ValueError(f"True value {true_value!r} not in domain")

    d = len(domain)
    p_truth = math.exp(epsilon) / (math.exp(epsilon) + d - 1)

    # Cryptographic random float in [0, 1)
    u = secrets.randbits(53) / (1 << 53)

    if u < p_truth:
        return true_value
    else:
        # Uniform random from domain (could be same as truth)
        idx = secrets.randbelow(d)
        return domain[idx]


def aggregate_randomized_responses(
    responses: list[str],
    domain: list[str],
    epsilon: float,
) -> dict[str, float]:
    """De-bias aggregated randomized responses to estimate true counts.

    Args:
        responses: Collected randomized responses.
        domain: The complete domain.
        epsilon: The epsilon used during collection.

    Returns:
        Estimated true count per category.
    """
    if not domain or epsilon <= 0:
        raise ValueError("Valid domain and positive epsilon are required")

    d = len(domain)
    n = len(responses)
    p = math.exp(epsilon) / (math.exp(epsilon) + d - 1)
    q = 1.0 / (math.exp(epsilon) + d - 1)

    observed: dict[str, int] = {v: 0 for v in domain}
    for r in responses:
        if r in observed:
            observed[r] += 1

    estimated: dict[str, float] = {}
    for v in domain:
        # De-bias: n_true = (observed - n*q) / (p - q)
        denominator = p - q
        if abs(denominator) < 1e-15:
            estimated[v] = observed[v]
        else:
            estimated[v] = max(0.0, (observed[v] - n * q) / denominator)

    return estimated


def estimate_frequencies(
    responses: list[str],
    domain: list[str],
    epsilon: float,
    n: int | None = None,
) -> dict[str, float]:
    """Estimate true frequency (proportion) per category.

    Args:
        responses: Collected randomized responses.
        domain: The domain.
        epsilon: The epsilon used.
        n: Total number of respondents (defaults to len(responses)).

    Returns:
        Estimated proportion per category, clamped to [0, 1] and normalized.
    """
    total = n if n is not None else len(responses)
    if total <= 0:
        raise ValueError("Population size must be positive")

    counts = aggregate_randomized_responses(responses, domain, epsilon)
    raw_freqs = {v: c / total for v, c in counts.items()}

    # Clamp to [0, 1]
    clamped = {v: max(0.0, min(1.0, f)) for v, f in raw_freqs.items()}

    # Normalize to sum to 1
    total_freq = sum(clamped.values())
    if total_freq > 0:
        return {v: f / total_freq for v, f in clamped.items()}
    # Fallback: uniform
    return {v: 1.0 / len(domain) for v in domain}
