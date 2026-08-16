"""Reconstruction attack against subset-count releases (Dinur-Nissim style).

The attack: generate a synthetic dataset where each row carries a secret
binary attribute (e.g. "has condition X"). Issue a batch of overlapping
random-subset count queries -- "how many rows with the secret attribute
fall inside this random subset of rows" -- and solve the resulting linear
system for the per-row secret bits.

This module runs the attack in two modes, and the distinction between them
is the entire point of including both.

**Unbudgeted mode** (`run_unbudgeted_attack`, `run_unbudgeted_suite`) gives
every query the same fixed epsilon, independent of how many queries are
issued. Dinur-Nissim (2003) shows that once the number of queries
comfortably exceeds the row count, a least-squares solve averages out *any*
noise whose magnitude does not grow with sqrt(n) -- which is exactly the
situation here, since `dp_core.private_count`'s Laplace scale is fixed by
epsilon and does not scale with query volume. Enough queries and a
"protected" release still gets reconstructed. This is a real and known
weakness of per-query epsilon taken in isolation, not a bug in `dp_core`,
and it is worth demonstrating on its own terms -- but it describes a
configuration the platform never actually permits, because
`reserve_privacy_budget` refuses once a policy's total epsilon is spent.
An unbudgeted-mode result at epsilon=2.0 across 200 queries has really
spent epsilon_total=400 against a policy whose default total is 5.0; the
platform would have stopped answering after two queries.

**Budgeted mode** (`run_budgeted_attack`, `run_budgeted_suite`) is the
demonstration that actually matches the deployed guarantee: fix a total
epsilon budget (default 5.0, matching `privacy_policies.epsilon_total`'s
default) and split it across k queries, so each query gets
`epsilon_total / k`. This is precisely what sequential composition buys:
as the attacker asks more questions, each answer gets noisier in exact
proportion, and the reconstruction accuracy this module measures should
stay near chance across the whole sweep of k -- more queries no longer
help, because the budget is fixed rather than the per-query cost. This is
the mode that should be read as the headline result; the unbudgeted mode
is context for why composition accounting matters at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.linalg import lstsq

from dp_core import private_count


@dataclass(frozen=True)
class ReconstructionResult:
    """Outcome of one reconstruction attack run.

    ``epsilon`` is always the epsilon actually spent on each individual
    query. For "dp_budgeted", that is ``epsilon_total / n_queries`` and
    ``epsilon_total`` is also populated; for "dp_unbudgeted" it is the
    fixed per-query epsilon the caller passed in and ``epsilon_total`` is
    left ``None`` because the attack does not track a shared budget.
    """

    condition: str  # "exact" | "dp_unbudgeted" | "dp_budgeted"
    n_rows: int
    n_queries: int
    accuracy: float  # fraction of secret bits recovered correctly
    epsilon: float | None = None  # None only for the exact-count condition
    epsilon_total: float | None = None  # populated only for "dp_budgeted"


def generate_secrets(n_rows: int, rng: np.random.Generator) -> np.ndarray:
    """A secret binary attribute per synthetic row.

    Uses numpy's Generator rather than `secrets`: this is synthetic *input
    data* for the attack, not a privacy mechanism, so a seedable PRNG is
    fine here -- and useful, since it makes attack runs reproducible.
    """
    return rng.integers(0, 2, size=n_rows)


def build_subset_queries(
    n_rows: int,
    n_queries: int,
    rng: np.random.Generator,
    inclusion_prob: float = 0.5,
) -> np.ndarray:
    """A random 0/1 design matrix: one row per query, one column per record.

    Random subsets keep the resulting linear system well conditioned without
    any adaptive query selection -- the classic Dinur-Nissim attack does not
    need to be clever about which subsets it asks for.
    """
    return rng.binomial(1, inclusion_prob, size=(n_queries, n_rows))


def _solve_and_score(
    design: np.ndarray, answers: np.ndarray, secrets_true: np.ndarray
) -> float:
    """Least-squares solve, round to the nearest bit, score against truth."""
    solution, *_ = lstsq(design.astype(float), answers.astype(float))
    recovered = np.clip(np.round(solution), 0, 1).astype(int)
    return float(np.mean(recovered == secrets_true))


def run_exact_attack(
    n_rows: int = 200,
    n_queries: int | None = None,
    seed: int | None = None,
) -> ReconstructionResult:
    """Reconstruct secret bits from exact (non-private) subset counts.

    ``n_queries`` defaults to ``n_rows`` -- the critical, minimally
    overdetermined regime described in the module docstring.
    """
    n_queries = n_queries or n_rows
    rng = np.random.default_rng(seed)
    secrets_true = generate_secrets(n_rows, rng)
    design = build_subset_queries(n_rows, n_queries, rng)
    exact_answers = design @ secrets_true
    accuracy = _solve_and_score(design, exact_answers, secrets_true)
    return ReconstructionResult("exact", n_rows, n_queries, accuracy)


def run_unbudgeted_attack(
    epsilon: float,
    n_rows: int = 200,
    n_queries: int | None = None,
    seed: int | None = None,
) -> ReconstructionResult:
    """Reconstruct secret bits from `dp_core.private_count` releases, one
    fixed epsilon per query, with no shared budget across queries.

    Each query is answered independently by the real mechanism. This audit
    deliberately does not track a shared privacy budget across the queries
    it issues -- the point is to characterize what a single epsilon-scaled
    release leaks per query, not to reproduce the platform's sequential
    composition accounting (which would refuse most of these queries long
    before the attack finished; see `run_budgeted_attack` for the mode that
    respects it). At `n_queries` queries this attack spends a real total of
    `epsilon * n_queries`, which readers should not mistake for a budget the
    platform would actually grant.
    """
    n_queries = n_queries or n_rows
    rng = np.random.default_rng(seed)
    secrets_true = generate_secrets(n_rows, rng)
    design = build_subset_queries(n_rows, n_queries, rng)
    exact_answers = design @ secrets_true
    dp_answers = np.array([private_count(int(v), epsilon) for v in exact_answers])
    accuracy = _solve_and_score(design, dp_answers, secrets_true)
    return ReconstructionResult("dp_unbudgeted", n_rows, n_queries, accuracy, epsilon=epsilon)


def run_budgeted_attack(
    epsilon_total: float,
    k: int,
    n_rows: int = 200,
    seed: int | None = None,
) -> ReconstructionResult:
    """Reconstruct secret bits from `dp_core.private_count` releases drawn
    from a FIXED total epsilon budget split evenly across k queries.

    This is the mode that matches what `reserve_privacy_budget` actually
    permits: a policy's `epsilon_total` (default 5.0) is fixed, and every
    query against it draws down the same pool. Splitting it evenly across
    k queries gives each one `epsilon_total / k` -- so asking more questions
    makes every individual answer noisier in exact proportion. That is
    sequential composition, and it is what should defeat this attack even
    as k grows, unlike the unbudgeted mode above.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    per_query_epsilon = epsilon_total / k
    rng = np.random.default_rng(seed)
    secrets_true = generate_secrets(n_rows, rng)
    design = build_subset_queries(n_rows, k, rng)
    exact_answers = design @ secrets_true
    dp_answers = np.array([private_count(int(v), per_query_epsilon) for v in exact_answers])
    accuracy = _solve_and_score(design, dp_answers, secrets_true)
    return ReconstructionResult(
        "dp_budgeted", n_rows, k, accuracy, epsilon=per_query_epsilon, epsilon_total=epsilon_total
    )


def run_unbudgeted_suite(
    epsilons: list[float],
    n_rows: int = 200,
    n_queries: int | None = None,
    seed: int | None = None,
) -> list[ReconstructionResult]:
    """Run the exact-baseline attack plus the unbudgeted DP attack at each epsilon."""
    results = [run_exact_attack(n_rows, n_queries, seed)]
    for epsilon in epsilons:
        results.append(run_unbudgeted_attack(epsilon, n_rows, n_queries, seed))
    return results


def run_budgeted_suite(
    epsilon_total: float,
    ks: list[int],
    n_rows: int = 200,
    seed: int | None = None,
) -> list[ReconstructionResult]:
    """Run the exact-baseline attack plus the budgeted DP attack at each k.

    The exact baseline uses `n_queries = n_rows` (the critical, minimally
    overdetermined regime), independent of the `ks` sweep, purely as a
    "what a noiseless attacker gets" reference point.
    """
    results = [run_exact_attack(n_rows, n_queries=n_rows, seed=seed)]
    for k in ks:
        results.append(run_budgeted_attack(epsilon_total, k, n_rows, seed))
    return results
