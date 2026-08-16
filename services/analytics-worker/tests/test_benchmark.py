from __future__ import annotations

import json

import pytest

from app import benchmark


def _fake_execute(path, query_type, epsilon, **kwargs):
    """Deterministic stand-in for app.executor.execute, shaped like the real responses.

    Values are picked so every query type's error against the ground truth
    computed by benchmark.compute_ground_truth on a synthesized 50-row,
    seed=1 dataset is a small, predictable, non-zero number -- enough to
    exercise the error-summary path without depending on real noise draws.
    """
    if query_type == "count":
        return {"value": 100, "mechanism": "laplace"}
    if query_type == "mean":
        return {"value": 40.0, "mechanism": "laplace"}
    if query_type == "bounded_sum":
        return {"value": 4000.0, "mechanism": "laplace"}
    if query_type == "histogram":
        return {"buckets": [{"value": v} for v in [10, 20, 15, 25, 15, 15]], "mechanism": "laplace"}
    if query_type in ("grouped_count", "top_k"):
        return {
            "values": [{"group": category, "value": 20} for category in benchmark.SEGMENT_CATEGORIES],
            "mechanism": "laplace",
        }
    raise AssertionError(f"unexpected query_type {query_type!r}")


# -- argument parsing --------------------------------------------------------


def test_parse_epsilons_rejects_non_positive():
    with pytest.raises(Exception):
        benchmark.parse_epsilons("0.5,0,1.0")


def test_parse_epsilons_rejects_garbage():
    with pytest.raises(Exception):
        benchmark.parse_epsilons("not-a-number")


def test_parse_epsilons_happy_path():
    assert benchmark.parse_epsilons("0.1, 0.5,1.0") == [0.1, 0.5, 1.0]


def test_parse_row_counts_rejects_non_positive():
    with pytest.raises(Exception):
        benchmark.parse_row_counts("100,0,50")


def test_parse_row_counts_happy_path():
    assert benchmark.parse_row_counts("100, 200") == [100, 200]


# -- synthetic dataset and ground truth --------------------------------------


def test_synthesize_dataset_shape(tmp_path):
    path = tmp_path / "synthetic.parquet"
    benchmark.synthesize_dataset(path, rows=25, seed=1)

    ground_truth = benchmark.compute_ground_truth(path)
    assert ground_truth["row_count"] == 25
    assert set(ground_truth["grouped_count"]) == set(benchmark.SEGMENT_CATEGORIES)
    assert sum(ground_truth["grouped_count"].values()) == 25
    assert sum(ground_truth["histogram"]) == 25
    assert len(ground_truth["histogram"]) == benchmark.HISTOGRAM_BINS
    assert benchmark.AMOUNT_LOWER <= ground_truth["mean"] <= benchmark.AMOUNT_UPPER


def test_compute_ground_truth_histogram_matches_executor_bin_count(tmp_path):
    """The bin count and total must agree with app.executor's own histogram query.

    This does not compare noisy values -- it checks that benchmark.py's
    independent binning logic (used only to score error) has not drifted
    out of sync with the executor's binning logic (used to answer queries).
    """
    from app.executor import execute as run_query

    path = tmp_path / "synthetic.parquet"
    benchmark.synthesize_dataset(path, rows=200, seed=1)
    ground_truth = benchmark.compute_ground_truth(path)

    response = run_query(
        path,
        "histogram",
        epsilon=10.0,
        value_column="amount",
        lower=benchmark.AMOUNT_LOWER,
        upper=benchmark.AMOUNT_UPPER,
        bins=benchmark.HISTOGRAM_BINS,
    )

    assert len(response["buckets"]) == len(ground_truth["histogram"]) == benchmark.HISTOGRAM_BINS
    assert sum(ground_truth["histogram"]) == 200


# -- error scoring ------------------------------------------------------------


def test_response_error_for_each_query_type():
    ground_truth = {
        "row_count": 100,
        "grouped_count": {"a": 10, "b": 20},
        "sum": 500.0,
        "mean": 50.0,
        "histogram": [5, 10, 15],
    }

    assert benchmark._response_error("count", {"value": 95}, ground_truth) == 5
    assert benchmark._response_error("mean", {"value": 48.0}, ground_truth) == 2.0
    assert benchmark._response_error("bounded_sum", {"value": 480.0}, ground_truth) == 20.0

    grouped_response = {"values": [{"group": "a", "value": 12}, {"group": "b", "value": 18}]}
    assert benchmark._response_error("grouped_count", grouped_response, ground_truth) == 2.0
    assert benchmark._response_error("top_k", grouped_response, ground_truth) == 2.0

    # A suppressed group (below the release threshold) contributes no term.
    assert benchmark._response_error("grouped_count", {"values": []}, ground_truth) == 0.0

    histogram_response = {"buckets": [{"value": 4}, {"value": 11}, {"value": 16}]}
    assert benchmark._response_error("histogram", histogram_response, ground_truth) == pytest.approx(1.0)

    with pytest.raises(ValueError):
        benchmark._response_error("unknown", {}, ground_truth)


def test_summarize_errors_quantiles_and_json_safety():
    summary = benchmark.summarize_errors([1.0, 2.0, 3.0, 4.0, 10.0])
    assert summary["mean_absolute_error"] == pytest.approx(4.0)
    assert summary["error_p50"] == 3.0
    assert summary["error_p90"] == 10.0
    assert summary["error_p99"] == 10.0
    json.dumps(summary)  # must not raise


def test_summarize_errors_coerces_numpy_scalars():
    """diffprivlib's mechanisms return np.float64; the summary must still be JSON-safe."""
    np = pytest.importorskip("numpy")
    summary = benchmark.summarize_errors([np.float64(1.5), np.float64(2.5)])
    assert isinstance(summary["mean_absolute_error"], float)
    json.dumps(summary)


def test_summarize_errors_empty():
    summary = benchmark.summarize_errors([])
    assert summary == {"mean_absolute_error": 0.0, "error_p50": 0.0, "error_p90": 0.0, "error_p99": 0.0}


# -- running queries ----------------------------------------------------------


def test_run_query_repeats_calls_execute_and_measures(monkeypatch, tmp_path):
    calls = []

    def fake_execute(path, query_type, epsilon, **kwargs):
        calls.append((path, query_type, epsilon, kwargs))
        return {"value": 10, "mechanism": "laplace"}

    monkeypatch.setattr(benchmark, "execute", fake_execute)
    config = benchmark.QueryConfig("count")
    responses, seconds, peak_memory = benchmark.run_query_repeats(tmp_path / "d.parquet", config, 0.5, 4)

    assert len(responses) == 4
    assert len(calls) == 4
    assert all(call[1] == "count" and call[2] == 0.5 for call in calls)
    assert seconds >= 0
    assert peak_memory >= 0


# -- build_report ---------------------------------------------------------


def test_build_report_covers_every_query_type_and_scopes_reference(monkeypatch):
    monkeypatch.setattr(benchmark, "execute", _fake_execute)

    report = benchmark.build_report([0.5, 1.0], row_counts=[50], repetitions=3, include_reference=True, seed=1)

    assert report["query_suite"] == "standard"
    assert report["epsilons"] == [0.5, 1.0]
    assert len(report["datasets"]) == 1

    dataset = report["datasets"][0]
    assert dataset["row_count"] == 50

    seen_query_types = {row["query_type"] for row in dataset["results"]}
    assert seen_query_types == {"count", "grouped_count", "mean", "bounded_sum", "histogram", "top_k"}
    assert len(dataset["results"]) == len(benchmark.QUERY_SUITES["standard"]) * 2

    for row in dataset["results"]:
        assert row["repetitions"] == 3
        assert row["mechanism"] == "laplace"
        assert row["mean_absolute_error"] >= 0
        assert row["peak_memory_bytes"] >= 0
        assert row["queries_per_second"] is None or row["queries_per_second"] > 0

    grouped_row = next(row for row in dataset["results"] if row["query_type"] == "grouped_count")
    assert grouped_row["group_count"] == len(benchmark.SEGMENT_CATEGORIES)
    count_row = next(row for row in dataset["results"] if row["query_type"] == "count")
    assert count_row["group_count"] is None

    # Only count/bounded_sum/mean get a third-party comparison column.
    reference_types = {entry["query_type"] for entry in dataset["reference_comparison"]}
    assert reference_types == set(benchmark.REFERENCE_QUERY_TYPES)
    assert len(dataset["reference_comparison"]) == len(benchmark.REFERENCE_QUERY_TYPES) * 2
    for entry in dataset["reference_comparison"]:
        assert "ours" in entry
        assert "diffprivlib" in entry or "diffprivlib_error" in entry

    assert report["package_versions"]["duckdb"] is not None
    json.dumps(report)  # the whole report must be JSON-serializable


def test_build_report_without_reference_comparison(monkeypatch):
    monkeypatch.setattr(benchmark, "execute", _fake_execute)

    report = benchmark.build_report([1.0], row_counts=[30], repetitions=2, include_reference=False, seed=1)

    dataset = report["datasets"][0]
    assert dataset["reference_comparison"] == []


def test_build_report_sweeps_multiple_row_counts(monkeypatch):
    monkeypatch.setattr(benchmark, "execute", _fake_execute)

    report = benchmark.build_report(
        [1.0], row_counts=[20, 40], repetitions=2, include_reference=False, seed=1
    )

    assert [dataset["row_count"] for dataset in report["datasets"]] == [20, 40]


def test_build_report_uses_provided_dataset(tmp_path):
    path = tmp_path / "real.parquet"
    benchmark.synthesize_dataset(path, rows=17, seed=2)

    report = benchmark.build_report([1.0], dataset=path, repetitions=2, include_reference=False)

    dataset = report["datasets"][0]
    assert dataset["row_count"] == 17
    assert dataset["dataset"] == str(path)


def test_build_report_rejects_unknown_suite():
    with pytest.raises(ValueError):
        benchmark.build_report([1.0], query_suite="does-not-exist")


# -- markdown report ------------------------------------------------------


def _sample_report() -> dict:
    return {
        "query_suite": "standard",
        "epsilons": [0.5, 1.0],
        "repetitions": 10,
        "runtime_seconds": 1.234,
        "python": "3.12.0 (test)\nmore",
        "hardware": {"platform": "TestOS", "processor": "TestCPU", "machine": "x86_64", "cpu_count": 4},
        "package_versions": {"veil-dp-core": "0.1.0", "diffprivlib": "0.6.6", "opendp": None},
        "datasets": [
            {
                "row_count": 100,
                "dataset": "synthetic.parquet",
                "results": [
                    {
                        "query_type": "count",
                        "epsilon": 0.5,
                        "epsilon_spent": 0.5,
                        "repetitions": 10,
                        "row_count": 100,
                        "group_count": None,
                        "runtime_seconds": 0.01,
                        "queries_per_second": 1000.0,
                        "peak_memory_bytes": 2048,
                        "mechanism": "laplace",
                        "mean_absolute_error": 2.0,
                        "error_p50": 1.5,
                        "error_p90": 4.0,
                        "error_p99": 4.0,
                    }
                ],
                "reference_comparison": [
                    {
                        "query_type": "count",
                        "epsilon": 0.5,
                        "row_count": 100,
                        "ours": {"mean_absolute_error": 2.0, "error_p50": 1.5, "error_p90": 4.0, "error_p99": 4.0},
                        "diffprivlib": {
                            "mean_absolute_error": 2.1,
                            "error_p50": 1.6,
                            "error_p90": 4.1,
                            "error_p99": 4.1,
                        },
                        "opendp": {"mean_absolute_error": 1.9, "error_p50": 1.4, "error_p90": 3.9, "error_p99": 3.9},
                    }
                ],
            }
        ],
    }


def test_markdown_report_contains_metadata_table_and_reference_section():
    markdown = benchmark.markdown_report(_sample_report())

    assert "**Query suite:** `standard`" in markdown
    assert "diffprivlib`: 0.6.6" in markdown
    assert "## Dataset: 100 rows" in markdown
    assert "| count | 0.5 |" in markdown
    assert "### Mean absolute error by epsilon" in markdown
    assert "### Reference-implementation comparison" in markdown
    assert "Reference comparison methodology" in markdown


# -- CLI --------------------------------------------------------------------


def test_cli_writes_json_and_markdown_from_output_prefix(monkeypatch, tmp_path, capsys):
    output = tmp_path / "benchmark-report"

    monkeypatch.setattr(benchmark, "build_report", lambda *args, **kwargs: _sample_report())
    monkeypatch.setattr("sys.argv", ["veil-benchmark", "--output", str(output)])

    benchmark.main()

    json_path = output.with_suffix(".json")
    md_path = output.with_suffix(".md")
    assert json.loads(json_path.read_text(encoding="utf-8"))["query_suite"] == "standard"
    assert md_path.exists()
    out = capsys.readouterr().out
    assert str(json_path) in out
    assert str(md_path) in out


def test_cli_passes_parsed_arguments_through(monkeypatch, tmp_path):
    captured = {}

    def fake_build_report(epsilons, **kwargs):
        captured["epsilons"] = epsilons
        captured.update(kwargs)
        return _sample_report()

    monkeypatch.setattr(benchmark, "build_report", fake_build_report)
    monkeypatch.setattr(
        "sys.argv",
        [
            "veil-benchmark",
            "--query-suite",
            "standard",
            "--epsilon",
            "0.2,0.8",
            "--rows",
            "10,20",
            "--repetitions",
            "5",
            "--output",
            str(tmp_path / "report"),
            "--no-reference",
        ],
    )

    benchmark.main()

    assert captured["epsilons"] == [0.2, 0.8]
    assert captured["row_counts"] == [10, 20]
    assert captured["repetitions"] == 5
    assert captured["include_reference"] is False


def test_cli_rejects_nonpositive_repetitions(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark, "build_report", lambda *args, **kwargs: _sample_report())
    monkeypatch.setattr(
        "sys.argv", ["veil-benchmark", "--repetitions", "0", "--output", str(tmp_path / "report")]
    )

    with pytest.raises(SystemExit):
        benchmark.main()
