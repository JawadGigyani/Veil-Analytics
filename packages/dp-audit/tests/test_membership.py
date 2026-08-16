"""Tests for the membership inference attack.

`dp_core.private_count` samples through `secrets`, not a seedable PRNG, so
DP-side AUC varies run to run; thresholds are set with margin against
observed variance rather than a single lucky run (see manual probes
referenced in the module docstring of `dp_audit.membership`).
"""

from __future__ import annotations

import numpy as np

from dp_audit.membership import (
    auc_from_scores,
    run_budgeted_membership_attack,
    run_budgeted_suite,
    run_exact_membership_attack,
    run_unbudgeted_membership_attack,
    run_unbudgeted_suite,
)

N_TRIALS = 150
BASE_COUNT = 20


class TestAucFromScores:
    def test_perfect_separation_gives_auc_one(self):
        scores_in = np.array([10.0, 11.0, 12.0])
        scores_out = np.array([1.0, 2.0, 3.0])
        assert auc_from_scores(scores_in, scores_out) == 1.0

    def test_identical_distributions_give_auc_half(self):
        scores = np.array([5.0, 5.0, 5.0, 5.0])
        assert auc_from_scores(scores, scores) == 0.5

    def test_reversed_separation_gives_auc_zero(self):
        scores_in = np.array([1.0, 2.0, 3.0])
        scores_out = np.array([10.0, 11.0, 12.0])
        assert auc_from_scores(scores_in, scores_out) == 0.0


class TestExactMembershipAttack:
    def test_exact_counts_are_perfectly_distinguishable(self):
        result = run_exact_membership_attack(base_count=BASE_COUNT, n_trials=N_TRIALS)
        assert result.condition == "exact"
        assert result.epsilon is None
        assert result.auc == 1.0


class TestUnbudgetedDpMembershipAttack:
    """Fixed epsilon per trial, no shared budget -- see
    `TestBudgetedDpMembershipAttack` for the mode that matches what the
    platform actually enforces."""

    def test_low_epsilon_auc_approaches_chance(self):
        result = run_unbudgeted_membership_attack(0.1, base_count=BASE_COUNT, n_trials=N_TRIALS)
        assert result.condition == "dp_unbudgeted"
        assert result.epsilon == 0.1
        assert result.epsilon_total is None
        # Chance is 0.5; comfortably below the AUC=1.0 exact case.
        assert result.auc < 0.7

    def test_dp_auc_is_below_exact_auc(self):
        exact = run_exact_membership_attack(base_count=BASE_COUNT, n_trials=N_TRIALS)
        dp = run_unbudgeted_membership_attack(1.0, base_count=BASE_COUNT, n_trials=N_TRIALS)
        assert dp.auc < exact.auc


class TestBudgetedDpMembershipAttack:
    """This is the mode that matches what the platform actually enforces:
    a fixed total epsilon split across the repeated trials the attack
    issues against each world. AUC should stay near chance across the
    whole sweep of trial counts, because running more trials to average
    away noise no longer helps -- each trial gets noisier in proportion."""

    def test_budgeted_attack_reports_epsilon_total_and_per_trial_epsilon(self):
        result = run_budgeted_membership_attack(epsilon_total=5.0, n_trials=50, base_count=BASE_COUNT)
        assert result.condition == "dp_budgeted"
        assert result.epsilon_total == 5.0
        assert result.n_trials == 50
        assert result.epsilon == 5.0 / 50

    def test_rejects_non_positive_trial_count(self):
        import pytest

        with pytest.raises(ValueError):
            run_budgeted_membership_attack(epsilon_total=5.0, n_trials=0, base_count=BASE_COUNT)

    def test_budgeted_sweep_stays_near_chance_across_trial_counts(self):
        """The regression guard for the platform's actual claim: under a
        fixed total budget, more repeated queries do not help the
        attacker average away the noise."""
        trial_counts = [20, 50, 100, 200]
        epsilon_total = 5.0
        aucs = [
            run_budgeted_membership_attack(epsilon_total, n, base_count=BASE_COUNT).auc
            for n in trial_counts
        ]
        # Chance is 0.5. Individual points carry noise (single draw per
        # trial count), so the guard checks the sweep's mean, with margin
        # verified against repeated manual runs.
        assert sum(aucs) / len(aucs) < 0.7
        # And no single point should look like the unbudgeted, high-epsilon
        # result (which reaches into the 0.8-1.0 range).
        assert all(auc < 0.8 for auc in aucs)


class TestUnbudgetedSuite:
    def test_suite_returns_exact_plus_one_result_per_epsilon(self):
        epsilons = [0.1, 0.5, 1.0]
        results = run_unbudgeted_suite(epsilons, base_count=BASE_COUNT, n_trials=N_TRIALS)
        assert len(results) == len(epsilons) + 1
        assert results[0].condition == "exact"
        assert [r.epsilon for r in results[1:]] == epsilons

    def test_all_aucs_are_valid_probabilities(self):
        results = run_unbudgeted_suite([0.1, 1.0], base_count=BASE_COUNT, n_trials=N_TRIALS)
        for r in results:
            assert 0.0 <= r.auc <= 1.0


class TestBudgetedSuite:
    def test_suite_returns_exact_plus_one_result_per_trial_count(self):
        trial_counts = [20, 50, 100]
        results = run_budgeted_suite(5.0, trial_counts, base_count=BASE_COUNT)
        assert len(results) == len(trial_counts) + 1
        assert results[0].condition == "exact"
        assert [r.n_trials for r in results[1:]] == trial_counts
        assert all(r.epsilon_total == 5.0 for r in results[1:])
