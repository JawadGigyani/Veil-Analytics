"""Membership inference attack via count differencing.

Setup: a target record either is or is not in the dataset. The attacker has
query access to a count that the target would affect if present -- for
example "how many rows match property P", where the target matches P. The
two possible worlds, "target in" and "target out", produce true counts that
differ by exactly 1: this is the standard add/remove-one neighbouring-
dataset pair that `dp_core`'s sensitivity analysis is calibrated against.

The attacker repeatedly queries each world and scores membership by the
released value itself (higher count => more likely "target in"). Attack
success is reported as AUC: the probability that a random "in" release
outranks a random "out" release. AUC = 1.0 means the attacker can always
tell; AUC = 0.5 means the releases are indistinguishable, i.e. chance.

Against exact counts the two worlds are separated by a constant offset of 1
with zero noise, so AUC is 1.0.

This module runs the DP side in two modes, mirroring `reconstruction.py`:

**Unbudgeted mode** (`run_unbudgeted_membership_attack`) gives every trial
the same fixed epsilon regardless of how many trials are run. As epsilon
falls the noise swamps the constant offset of 1 and AUC decays toward 0.5
-- but this describes an attacker asking `n_trials` independent full-price
queries, which `reserve_privacy_budget` would never fund past a couple of
trials at any epsilon the platform actually issues.

**Budgeted mode** (`run_budgeted_membership_attack`) fixes a total epsilon
budget (default 5.0, matching `privacy_policies.epsilon_total`'s default)
and splits it across the `n_trials` repeated queries the attack issues
against each world, so each trial gets `epsilon_total / n_trials`. This is
what an attacker who has to live inside one policy's real budget actually
gets: more repetitions do not help, because each repetition is noisier in
exact proportion to how many there are. This is the mode that matches the
deployed guarantee and should be read as the headline result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

from dp_core import private_count


@dataclass(frozen=True)
class MembershipResult:
    """Outcome of one membership inference attack run.

    ``epsilon`` is always the epsilon actually spent per trial. For
    "dp_budgeted", that is ``epsilon_total / n_trials`` and ``epsilon_total``
    is also populated; for "dp_unbudgeted" it is the fixed per-trial epsilon
    the caller passed in and ``epsilon_total`` is left ``None``.
    """

    condition: str  # "exact" | "dp_unbudgeted" | "dp_budgeted"
    n_trials: int
    auc: float
    epsilon: float | None = None  # None only for the exact-count condition
    epsilon_total: float | None = None  # populated only for "dp_budgeted"


def auc_from_scores(scores_in: np.ndarray, scores_out: np.ndarray) -> float:
    """Mann-Whitney U / rank-sum AUC between two score samples.

    Avoids depending on scikit-learn for a single statistic: AUC equals the
    probability that a random draw from ``scores_in`` exceeds a random draw
    from ``scores_out`` (ties count as a half), which is exactly the
    normalized rank-sum statistic below.
    """
    n_in, n_out = len(scores_in), len(scores_out)
    ranks = rankdata(np.concatenate([scores_in, scores_out]))
    rank_sum_in = ranks[:n_in].sum()
    return float((rank_sum_in - n_in * (n_in + 1) / 2) / (n_in * n_out))


def run_exact_membership_attack(
    base_count: int = 50,
    n_trials: int = 300,
) -> MembershipResult:
    """Attack exact counts: the target's presence shifts the count by 1."""
    count_out = base_count
    count_in = base_count + 1
    scores_in = np.full(n_trials, count_in, dtype=float)
    scores_out = np.full(n_trials, count_out, dtype=float)
    auc = auc_from_scores(scores_in, scores_out)
    return MembershipResult("exact", n_trials, auc)


def run_unbudgeted_membership_attack(
    epsilon: float,
    base_count: int = 50,
    n_trials: int = 300,
) -> MembershipResult:
    """Attack `dp_core.private_count` releases, one fixed epsilon per trial,
    with no shared budget across the ``n_trials`` repeated queries.

    Matches how an analyst would query the mechanism a single time, but
    does not simulate budget accounting across repeated queries -- see
    `run_budgeted_membership_attack` for the mode that does. There is no
    ``seed`` parameter: the mechanism samples from `secrets`, not a
    seedable PRNG, so a run is not reproducible bit-for-bit -- only
    statistically.
    """
    count_out = base_count
    count_in = base_count + 1
    scores_in = np.array([private_count(count_in, epsilon) for _ in range(n_trials)], dtype=float)
    scores_out = np.array([private_count(count_out, epsilon) for _ in range(n_trials)], dtype=float)
    auc = auc_from_scores(scores_in, scores_out)
    return MembershipResult("dp_unbudgeted", n_trials, auc, epsilon=epsilon)


def run_budgeted_membership_attack(
    epsilon_total: float,
    n_trials: int,
    base_count: int = 50,
) -> MembershipResult:
    """Attack `dp_core.private_count` releases drawn from a FIXED total
    epsilon budget split evenly across the ``n_trials`` repeated queries
    the attack issues against each world.

    This is the mode that matches what `reserve_privacy_budget` actually
    permits: the attacker has `epsilon_total` to spend in total (default
    5.0, the platform's default policy budget) and divides it across
    `n_trials` repetitions, each costing `epsilon_total / n_trials`. Running
    more trials to average away noise no longer helps, because each trial
    is noisier in exact proportion to how many there are.
    """
    if n_trials <= 0:
        raise ValueError("n_trials must be positive")
    per_trial_epsilon = epsilon_total / n_trials
    count_out = base_count
    count_in = base_count + 1
    scores_in = np.array(
        [private_count(count_in, per_trial_epsilon) for _ in range(n_trials)], dtype=float
    )
    scores_out = np.array(
        [private_count(count_out, per_trial_epsilon) for _ in range(n_trials)], dtype=float
    )
    auc = auc_from_scores(scores_in, scores_out)
    return MembershipResult(
        "dp_budgeted", n_trials, auc, epsilon=per_trial_epsilon, epsilon_total=epsilon_total
    )


def run_unbudgeted_suite(
    epsilons: list[float],
    base_count: int = 50,
    n_trials: int = 300,
) -> list[MembershipResult]:
    """Run the exact-baseline attack plus the unbudgeted DP attack at each epsilon."""
    results = [run_exact_membership_attack(base_count, n_trials)]
    for epsilon in epsilons:
        results.append(run_unbudgeted_membership_attack(epsilon, base_count, n_trials))
    return results


def run_budgeted_suite(
    epsilon_total: float,
    trial_counts: list[int],
    base_count: int = 50,
) -> list[MembershipResult]:
    """Run the exact-baseline attack plus the budgeted DP attack at each trial count.

    The exact baseline uses the largest requested trial count purely as a
    "what a noiseless attacker gets" reference point.
    """
    reference_trials = max(trial_counts) if trial_counts else 300
    results = [run_exact_membership_attack(base_count, reference_trials)]
    for n_trials in trial_counts:
        results.append(run_budgeted_membership_attack(epsilon_total, n_trials, base_count))
    return results
