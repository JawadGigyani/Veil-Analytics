"""Tests for the CLI entry point.

Runs with small sample counts and a small k-sweep (via the CLI's own flags)
so the full report generates in well under a second; the CLI's *defaults*
are sized for a convincing report, not for test speed.
"""

from __future__ import annotations

import json

from dp_audit.run import build_report, main, parse_epsilons, parse_int_list, render_markdown

SMALL_K_SWEEP = [10, 20, 30]


def _small_report(epsilons):
    return build_report(
        epsilons,
        recon_rows=30,
        recon_queries=30,
        membership_trials=50,
        membership_base_count=15,
        epsilon_samples=300,
        epsilon_bins=15,
        epsilon_alpha=0.05,
        epsilon_tolerance=0.05,
        epsilon_total=5.0,
        k_sweep=SMALL_K_SWEEP,
        seed=1,
    )


class TestParseEpsilons:
    def test_parses_comma_separated_floats(self):
        assert parse_epsilons("0.1,0.5,1.0,2.0") == [0.1, 0.5, 1.0, 2.0]

    def test_tolerates_whitespace(self):
        assert parse_epsilons(" 0.1, 0.5 ,1.0") == [0.1, 0.5, 1.0]

    def test_rejects_empty_input(self):
        import pytest

        with pytest.raises(ValueError):
            parse_epsilons("")


class TestParseIntList:
    def test_parses_comma_separated_ints(self):
        assert parse_int_list("10,50,100") == [10, 50, 100]

    def test_rejects_empty_input(self):
        import pytest

        with pytest.raises(ValueError):
            parse_int_list("")


class TestBuildReport:
    def test_report_has_all_sections(self):
        report = _small_report([0.5, 1.0])
        assert "reconstruction_unbudgeted" in report
        assert "reconstruction_budgeted" in report
        assert "membership_inference_unbudgeted" in report
        assert "membership_inference_budgeted" in report
        assert "epsilon_estimation" in report
        assert len(report["reconstruction_unbudgeted"]) == 3  # exact + 2 epsilons
        assert len(report["reconstruction_budgeted"]) == len(SMALL_K_SWEEP) + 1
        assert len(report["membership_inference_unbudgeted"]) == 3
        assert len(report["membership_inference_budgeted"]) == len(SMALL_K_SWEEP) + 1
        assert len(report["epsilon_estimation"]) == 6  # 3 mechanisms x 2 epsilons

    def test_budgeted_results_carry_epsilon_total(self):
        report = _small_report([1.0])
        for r in report["reconstruction_budgeted"][1:]:  # skip the exact baseline
            assert r["epsilon_total"] == 5.0
        for r in report["membership_inference_budgeted"][1:]:
            assert r["epsilon_total"] == 5.0

    def test_report_is_json_serializable(self):
        report = _small_report([1.0])
        json.dumps(report)  # must not raise


class TestRenderMarkdown:
    def test_markdown_contains_the_headings_and_both_modes(self):
        report = _small_report([1.0])
        md = render_markdown(report)
        assert "## Reconstruction attack" in md
        assert "## Membership inference attack" in md
        assert "## Empirical epsilon estimation" in md
        assert "Unbudgeted" in md
        assert "Budgeted" in md
        # Plain markdown tables only -- no external assets or plotting output.
        assert "<img" not in md
        assert "![" not in md


class TestMainEndToEnd:
    def test_writes_json_and_markdown_and_exits_zero(self, tmp_path):
        output_prefix = str(tmp_path / "report")
        exit_code = main(
            [
                "--epsilon",
                "0.5,1.0",
                "--output",
                output_prefix,
                "--seed",
                "1",
                "--recon-rows",
                "30",
                "--recon-queries",
                "30",
                "--membership-trials",
                "50",
                "--membership-base-count",
                "15",
                "--epsilon-total",
                "5.0",
                "--k-sweep",
                "10,20,30",
                "--epsilon-samples",
                "400",
                "--epsilon-bins",
                "15",
            ]
        )
        assert exit_code == 0

        json_path = tmp_path / "report.json"
        md_path = tmp_path / "report.md"
        assert json_path.exists()
        assert md_path.exists()

        report = json.loads(json_path.read_text(encoding="utf-8"))
        assert report["epsilons"] == [0.5, 1.0]
        assert report["parameters"]["budgeted"]["epsilon_total"] == 5.0
        assert report["parameters"]["budgeted"]["k_sweep"] == [10, 20, 30]

        md = md_path.read_text(encoding="utf-8")
        assert "# dp-audit report" in md
        assert "accuracy" in md
        assert "AUC" in md
