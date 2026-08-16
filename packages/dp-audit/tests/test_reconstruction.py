"""Tests for the reconstruction attack.

Uses small row/query counts to keep runtime low. `dp_core.private_count`
samples through `secrets`, not a seedable PRNG, so the DP-side accuracy
numbers vary run to run; thresholds below are set with margin against the
observed variance (see the module docstring in `dp_audit.reconstruction`
and the manual probes used to pick them), not tuned to a single lucky run.
"""

from __future__ import annotations

from dp_audit.reconstruction import (
    build_subset_queries,
    generate_secrets,
    run_budgeted_attack,
    run_budgeted_suite,
    run_exact_attack,
    run_unbudgeted_attack,
    run_unbudgeted_suite,
)

N_ROWS = 50  # square regime: n_queries defaults to n_rows


class TestBuildingBlocks:
    def test_generate_secrets_is_binary(self):
        import numpy as np

        rng = np.random.default_rng(0)
        secrets_true = generate_secrets(N_ROWS, rng)
        assert set(np.unique(secrets_true)).issubset({0, 1})
        assert len(secrets_true) == N_ROWS

    def test_build_subset_queries_shape(self):
        import numpy as np

        rng = np.random.default_rng(0)
        design = build_subset_queries(N_ROWS, N_ROWS, rng)
        assert design.shape == (N_ROWS, N_ROWS)
        assert set(np.unique(design)).issubset({0, 1})


class TestExactReconstruction:
    def test_exact_counts_reconstruct_near_perfectly(self):
        result = run_exact_attack(n_rows=N_ROWS, seed=1)
        assert result.condition == "exact"
        assert result.epsilon is None
        assert result.accuracy >= 0.98

    def test_exact_reconstruction_is_deterministic_given_seed(self):
        a = run_exact_attack(n_rows=N_ROWS, seed=42)
        b = run_exact_attack(n_rows=N_ROWS, seed=42)
        assert a.accuracy == b.accuracy


class TestUnbudgetedDpReconstruction:
    """Fixed epsilon per query, no shared budget -- a real weakness of
    per-query epsilon in isolation, but a query volume the platform would
    never actually fund (see `TestBudgetedDpReconstruction` below for the
    mode that respects the platform's enforced budget)."""

    def test_low_epsilon_collapses_toward_chance(self):
        result = run_unbudgeted_attack(0.1, n_rows=N_ROWS, seed=7)
        assert result.condition == "dp_unbudgeted"
        assert result.epsilon == 0.1
        assert result.epsilon_total is None
        # Chance is 0.5; well below the near-100% exact accuracy.
        assert result.accuracy < 0.75

    def test_dp_accuracy_is_far_below_exact_accuracy(self):
        exact = run_exact_attack(n_rows=N_ROWS, seed=3)
        dp = run_unbudgeted_attack(1.0, n_rows=N_ROWS, seed=3)
        assert dp.accuracy < exact.accuracy - 0.15


class TestBudgetedDpReconstruction:
    """This is the mode that matches what the platform actually enforces:
    a fixed total epsilon split across k queries. Accuracy should stay
    near chance across the whole sweep of k, because more queries no
    longer help -- each one gets noisier in exact proportion."""

    def test_budgeted_attack_reports_epsilon_total_and_per_query_epsilon(self):
        result = run_budgeted_attack(epsilon_total=5.0, k=50, n_rows=N_ROWS, seed=1)
        assert result.condition == "dp_budgeted"
        assert result.epsilon_total == 5.0
        assert result.n_queries == 50
        assert result.epsilon == 5.0 / 50

    def test_rejects_non_positive_k(self):
        import pytest

        with pytest.raises(ValueError):
            run_budgeted_attack(epsilon_total=5.0, k=0, n_rows=N_ROWS)

    def test_budgeted_sweep_stays_near_chance_across_k(self):
        """The regression guard for the platform's actual claim: under a
        fixed total budget, more queries do not help the attacker."""
        ks = [10, 50, 100, 200]
        epsilon_total = 5.0
        accuracies = [
            run_budgeted_attack(epsilon_total, k, n_rows=N_ROWS, seed=k).accuracy for k in ks
        ]
        # Chance is 0.5. Individual points carry noise (small n_rows, single
        # draw per k), so the guard checks the sweep's mean rather than any
        # one point, with margin verified against repeated manual runs.
        assert sum(accuracies) / len(accuracies) < 0.7
        # And no single point should look like the unbudgeted, high-epsilon
        # result (which reaches into the 0.9-1.0 range).
        assert all(acc < 0.85 for acc in accuracies)


class TestUnbudgetedSuite:
    def test_suite_returns_exact_plus_one_result_per_epsilon(self):
        epsilons = [0.1, 0.5, 1.0]
        results = run_unbudgeted_suite(epsilons, n_rows=N_ROWS, seed=5)
        assert len(results) == len(epsilons) + 1
        assert results[0].condition == "exact"
        assert [r.epsilon for r in results[1:]] == epsilons

    def test_suite_accuracy_never_exceeds_exact(self):
        results = run_unbudgeted_suite([0.1, 0.5, 1.0, 2.0], n_rows=N_ROWS, seed=9)
        exact_accuracy = results[0].accuracy
        for r in results[1:]:
            assert r.accuracy <= exact_accuracy + 1e-9


class TestBudgetedSuite:
    def test_suite_returns_exact_plus_one_result_per_k(self):
        ks = [10, 50, 100]
        results = run_budgeted_suite(5.0, ks, n_rows=N_ROWS, seed=5)
        assert len(results) == len(ks) + 1
        assert results[0].condition == "exact"
        assert [r.n_queries for r in results[1:]] == ks
        assert all(r.epsilon_total == 5.0 for r in results[1:])
