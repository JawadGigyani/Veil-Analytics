"""Exponential mechanism and report-noisy-max.

The exponential mechanism selects an outcome with probability proportional to
exp(ε × score / (2 × sensitivity)).  Report-noisy-max adds Laplace noise to
each score and returns the argmax.  Both satisfy ε-differential privacy.
"""

from __future__ import annotations

import math
import secrets

from dp_core.mechanisms.laplace import laplace_noise


def exponential_mechanism(
    scores: list[float],
    sensitivity: float,
    epsilon: float,
) -> int:
    """Select an index using the exponential mechanism.

    Args:
        scores: Quality scores for each candidate.
        sensitivity: Global sensitivity of the scoring function.
        epsilon: Privacy parameter.

    Returns:
        Selected index, sampled proportional to exp(ε × score / (2Δ)).
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if sensitivity <= 0:
        raise ValueError("Sensitivity must be positive")
    if not scores:
        raise ValueError("Scores must be non-empty")

    # Compute log-probabilities for numerical stability
    scale = epsilon / (2.0 * sensitivity)
    max_score = max(scores)
    log_weights = [(s - max_score) * scale for s in scores]

    # Convert to probabilities
    weights = [math.exp(lw) for lw in log_weights]
    total = sum(weights)
    probabilities = [w / total for w in weights]

    # Sample using cryptographic randomness
    u = secrets.randbits(53) / (1 << 53)
    cumulative = 0.0
    for i, p in enumerate(probabilities):
        cumulative += p
        if u < cumulative:
            return i

    # Fallback for floating-point edge case
    return len(scores) - 1


def noisy_argmax(
    counts: list[float],
    epsilon: float,
) -> int:
    """Report-noisy-max: add Laplace(1/ε) noise to each count, return argmax.

    This satisfies ε-differential privacy when each count has sensitivity 1.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if not counts:
        raise ValueError("Counts must be non-empty")

    noisy = [c + laplace_noise(1.0 / epsilon) for c in counts]
    return max(range(len(noisy)), key=lambda i: noisy[i])


def private_top_k(
    counts: list[float],
    labels: list[str],
    k: int,
    epsilon: float,
) -> list[dict[str, object]]:
    """Privately select the top-k categories using iterative noisy-max.

    Splits ε evenly across k selections.  Each selection uses
    report-noisy-max and the selected item is removed from further
    consideration.  The released counts also use Laplace noise
    (with the remaining ε/2 per item).

    Args:
        counts: True counts per category.
        labels: Category labels (same order as counts).
        k: Number of top items to release.
        epsilon: Total privacy budget.

    Returns:
        List of dicts with ``group``, ``value``, and ``range`` keys,
        sorted by noisy value descending.
    """
    if epsilon <= 0:
        raise ValueError("Epsilon must be positive")
    if k <= 0:
        raise ValueError("k must be positive")
    if len(counts) != len(labels):
        raise ValueError("Counts and labels must have the same length")
    if not counts:
        return []

    actual_k = min(k, len(counts))
    # Split budget: half for selection, half for releasing counts
    selection_epsilon = epsilon / 2
    release_epsilon = epsilon / 2
    per_selection = selection_epsilon / actual_k if actual_k > 0 else selection_epsilon

    remaining_counts = list(counts)
    remaining_labels = list(labels)
    results: list[dict[str, object]] = []

    for _ in range(actual_k):
        if not remaining_counts:
            break

        # Select using noisy-max
        idx = noisy_argmax(remaining_counts, per_selection)

        # Release a noisy count
        noisy_count = max(0, round(
            remaining_counts[idx] + laplace_noise(1.0 / release_epsilon)
        ))

        results.append({
            "group": remaining_labels[idx],
            "value": noisy_count,
            "range": round(math.log(20) / release_epsilon, 2),
        })

        # Remove selected item
        remaining_counts.pop(idx)
        remaining_labels.pop(idx)

    results.sort(key=lambda r: r["value"], reverse=True)  # type: ignore[arg-type]
    return results
