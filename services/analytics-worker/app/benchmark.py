"""Benchmark private query execution against a reference implementation.

Covers every query type ``app.executor.execute`` supports (not just
``count``) and, for the query types where a third party offers a directly
comparable mechanism, adds ``diffprivlib`` (and ``opendp`` where its API
cleanly matches) as a reference column at matched epsilon and matched
sensitivity. Matching our own tests would only prove the code does what the
code intends; matching an independent implementation is evidence the
calibration itself is right. See docs/enhancement-plan.md, section B.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as parquet

DEFAULT_EPSILONS = (0.1, 0.5, 1.0, 2.0)
DEFAULT_ROW_COUNTS = (2_000,)
DEFAULT_REPETITIONS = 200
DEFAULT_SEED = 20240817

# Synthetic dataset shape shared by every query in the "standard" suite, so a
# single generated file drives count, grouped_count, mean, bounded_sum,
# histogram, and top_k without special-casing any one of them.
SEGMENT_CATEGORIES = ["north", "south", "east", "west", "central"]
AMOUNT_LOWER, AMOUNT_UPPER = 0.0, 100.0
HISTOGRAM_BINS = 6

# diffprivlib and opendp both offer a direct, matched-sensitivity Laplace
# comparison for these three query types. grouped_count, histogram, and
# top_k are left to our own mechanism only -- see markdown_report's
# methodology note for why forcing a comparison there would not be clean.
REFERENCE_QUERY_TYPES = ("count", "bounded_sum", "mean")


def execute(*args, **kwargs):
    """Load the data-processing dependency only when a benchmark is run."""
    from .executor import execute as run_query

    return run_query(*args, **kwargs)


def parse_epsilons(value: str) -> list[float]:
    try:
        epsilons = [float(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("epsilon values must be comma-separated numbers") from error
    if not epsilons or any(epsilon <= 0 for epsilon in epsilons):
        raise argparse.ArgumentTypeError("epsilon values must be greater than zero")
    return epsilons


def parse_row_counts(value: str) -> list[int]:
    try:
        counts = [int(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("row counts must be comma-separated integers") from error
    if not counts or any(count <= 0 for count in counts):
        raise argparse.ArgumentTypeError("row counts must be greater than zero")
    return counts


@dataclass(frozen=True)
class QueryConfig:
    """One query in a benchmark suite, with the extra arguments ``execute`` needs.

    ``execute`` requires ``group_categories`` for grouped/top_k releases and
    ``lower``/``upper`` for mean/bounded_sum/histogram -- those requirements
    are baked into each config below rather than rediscovered by trial and
    error at call time.
    """

    query_type: str
    kwargs: dict = field(default_factory=dict)
    group_count: int | None = None


QUERY_SUITES: dict[str, list[QueryConfig]] = {
    "standard": [
        QueryConfig("count"),
        QueryConfig(
            "grouped_count",
            {"group_by": "segment", "group_categories": SEGMENT_CATEGORIES},
            group_count=len(SEGMENT_CATEGORIES),
        ),
        QueryConfig("mean", {"value_column": "amount", "lower": AMOUNT_LOWER, "upper": AMOUNT_UPPER}),
        QueryConfig("bounded_sum", {"value_column": "amount", "lower": AMOUNT_LOWER, "upper": AMOUNT_UPPER}),
        QueryConfig(
            "histogram",
            {"value_column": "amount", "lower": AMOUNT_LOWER, "upper": AMOUNT_UPPER, "bins": HISTOGRAM_BINS},
            group_count=HISTOGRAM_BINS,
        ),
        QueryConfig(
            "top_k",
            {"group_by": "segment", "group_categories": SEGMENT_CATEGORIES, "top_k": 3},
            group_count=len(SEGMENT_CATEGORIES),
        ),
    ],
}


def synthesize_dataset(path: Path, rows: int, seed: int = DEFAULT_SEED) -> None:
    """Write a synthetic Parquet file covering every query in the standard suite.

    The dataset's *content* is generated with a fixed seed so benchmark runs
    are comparable across invocations -- only the noise the mechanisms add
    needs to be unpredictable, never the underlying data.
    """
    rng = random.Random(seed)
    table = pa.table(
        {
            "segment": [rng.choice(SEGMENT_CATEGORIES) for _ in range(rows)],
            "amount": [rng.uniform(AMOUNT_LOWER, AMOUNT_UPPER) for _ in range(rows)],
        }
    )
    parquet.write_table(table, path)


def compute_ground_truth(path: Path) -> dict:
    """Compute the exact (non-private) statistics the benchmark scores error against.

    The histogram bin edges mirror ``app.executor``'s binning logic exactly
    (same step size, same clamp to ``[0, bins - 1]``) so a reported error
    never mixes in a disagreement between this function and the code under
    test.
    """
    con = duckdb.connect()
    try:
        row_count = con.execute("select count(*) from read_parquet(?)", [str(path)]).fetchone()[0]
        grouped = dict(
            con.execute(
                "select cast(segment as varchar), count(*) from read_parquet(?) group by 1", [str(path)]
            ).fetchall()
        )
        total, mean = con.execute("select sum(amount), avg(amount) from read_parquet(?)", [str(path)]).fetchone()
        amounts = [row[0] for row in con.execute("select amount from read_parquet(?)", [str(path)]).fetchall()]
    finally:
        con.close()

    step = (AMOUNT_UPPER - AMOUNT_LOWER) / HISTOGRAM_BINS
    bin_counts = [0] * HISTOGRAM_BINS
    for value in amounts:
        if not math.isfinite(value):
            continue
        index = min(HISTOGRAM_BINS - 1, max(0, int((value - AMOUNT_LOWER) / step)))
        bin_counts[index] += 1

    return {
        "row_count": row_count,
        "grouped_count": {category: grouped.get(category, 0) for category in SEGMENT_CATEGORIES},
        "sum": float(total) if total is not None else 0.0,
        "mean": float(mean) if mean is not None else 0.0,
        "histogram": bin_counts,
        "amounts": amounts,
    }


def _response_error(query_type: str, response: dict, ground_truth: dict) -> float:
    """Absolute error of one released response against the true statistic.

    For grouped/histogram/top_k releases this averages the per-item error
    across whatever the mechanism actually returned. A suppressed group
    contributes no term -- a deliberate simplification, not a hidden one; see
    the reference-comparison scope note for the same reasoning applied to
    which query types get a third-party column at all.
    """
    if query_type == "count":
        return abs(response["value"] - ground_truth["row_count"])
    if query_type == "mean":
        return abs(response["value"] - ground_truth["mean"])
    if query_type == "bounded_sum":
        return abs(response["value"] - ground_truth["sum"])
    if query_type in ("grouped_count", "top_k"):
        items = response["values"]
        if not items:
            return 0.0
        return statistics.fmean(
            abs(item["value"] - ground_truth["grouped_count"].get(item["group"], 0)) for item in items
        )
    if query_type == "histogram":
        buckets = response["buckets"]
        return statistics.fmean(
            abs(bucket["value"] - true_count) for bucket, true_count in zip(buckets, ground_truth["histogram"])
        )
    raise ValueError(f"Unknown query_type {query_type!r}")


def _quantile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank quantile -- adequate for a benchmark report, not a statistics library."""
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, math.ceil(q * len(sorted_values)) - 1))
    return sorted_values[index]


def summarize_errors(errors: list[float]) -> dict:
    # diffprivlib returns numpy scalars (np.float64); coercing to plain
    # floats here -- once, for every caller -- keeps the report JSON-safe
    # without every reference helper needing to remember to do it.
    errors = [float(value) for value in errors]
    ordered = sorted(errors)
    return {
        "mean_absolute_error": round(statistics.fmean(errors), 4) if errors else 0.0,
        "error_p50": round(_quantile(ordered, 0.50), 4),
        "error_p90": round(_quantile(ordered, 0.90), 4),
        "error_p99": round(_quantile(ordered, 0.99), 4),
    }


def run_query_repeats(
    path: Path, config: QueryConfig, epsilon: float, repetitions: int
) -> tuple[list[dict], float, int]:
    """Execute a query ``repetitions`` times through ``app.executor.execute``.

    Returns ``(responses, wall_seconds, peak_memory_bytes)``. ``tracemalloc``
    tracks allocations made through Python's allocator only -- it does not
    see memory duckdb's C extension allocates outside of it -- so
    ``peak_memory_bytes`` is a lower bound on actual peak RSS, not a full
    process measurement.
    """
    tracemalloc.start()
    started = time.perf_counter()
    responses = [execute(path, config.query_type, epsilon, **config.kwargs) for _ in range(repetitions)]
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return responses, seconds, peak


def _load_diffprivlib():
    """Import diffprivlib, working around a packaging break in 0.6.6.

    diffprivlib's top-level ``__init__`` unconditionally imports
    ``diffprivlib.models``, which imports scikit-learn's private tree
    internals (``sklearn.tree._tree.Tree`` and friends). Those internals were
    removed in scikit-learn >= 1.5, so a plain ``import diffprivlib`` raises
    ``ImportError`` on any environment with a current scikit-learn -- even
    though this module only needs ``diffprivlib.mechanisms`` and
    ``diffprivlib.tools``, neither of which touches the ML models submodule.
    Pre-registering a stub module lets the package import skip the
    incompatible submodule instead of failing outright.
    """
    import sys
    import types

    if "diffprivlib.models" not in sys.modules:
        sys.modules["diffprivlib.models"] = types.ModuleType("diffprivlib.models")
    import diffprivlib

    return diffprivlib


def _reference_count(epsilon: float, true_count: int, repetitions: int) -> dict:
    """diffprivlib and opendp columns for a count query at matched epsilon/sensitivity."""
    result: dict = {}
    try:
        diffprivlib = _load_diffprivlib()
        mechanism = diffprivlib.mechanisms.Laplace(epsilon=epsilon, sensitivity=1.0)
        errors = [abs(mechanism.randomise(true_count) - true_count) for _ in range(repetitions)]
        result["diffprivlib"] = summarize_errors(errors)
    except Exception as error:  # pragma: no cover - environment-dependent
        result["diffprivlib_error"] = str(error)

    try:
        import opendp.prelude as dp

        dp.enable_features("contrib")
        space = dp.vector_domain(dp.atom_domain(T=float)), dp.symmetric_distance()
        count_trans = space >> dp.t.then_count()
        measurement = dp.binary_search_chain(
            lambda scale: count_trans >> dp.m.then_laplace(scale=scale), d_in=1, d_out=epsilon
        )
        data = [0.0] * true_count
        errors = [abs(measurement(data) - true_count) for _ in range(repetitions)]
        result["opendp"] = summarize_errors(errors)
    except Exception as error:  # pragma: no cover - environment-dependent
        result["opendp_error"] = str(error)

    return result


def _reference_bounded_sum(
    epsilon: float, lower: float, upper: float, true_sum: float, amounts: list[float], repetitions: int
) -> dict:
    """diffprivlib and opendp columns for a bounded-sum query at matched epsilon/sensitivity."""
    sensitivity = max(abs(lower), abs(upper))
    result: dict = {}
    try:
        diffprivlib = _load_diffprivlib()
        mechanism = diffprivlib.mechanisms.Laplace(epsilon=epsilon, sensitivity=sensitivity)
        errors = [abs(mechanism.randomise(true_sum) - true_sum) for _ in range(repetitions)]
        result["diffprivlib"] = summarize_errors(errors)
    except Exception as error:  # pragma: no cover - environment-dependent
        result["diffprivlib_error"] = str(error)

    try:
        import opendp.prelude as dp

        dp.enable_features("contrib")
        space = dp.vector_domain(dp.atom_domain(T=float, nan=False)), dp.symmetric_distance()
        sum_trans = (space >> dp.t.then_clamp(bounds=(lower, upper))) >> dp.t.then_sum()
        measurement = dp.binary_search_chain(
            lambda scale: sum_trans >> dp.m.then_laplace(scale=scale), d_in=1, d_out=epsilon
        )
        errors = [abs(measurement(amounts) - true_sum) for _ in range(repetitions)]
        result["opendp"] = summarize_errors(errors)
    except Exception as error:  # pragma: no cover - environment-dependent
        result["opendp_error"] = str(error)

    return result


def _reference_mean(
    epsilon: float, lower: float, upper: float, true_mean: float, amounts: list[float], repetitions: int
) -> dict:
    """diffprivlib column for a mean query at matched epsilon and public bounds.

    opendp is skipped here on purpose: its mean transformation requires a
    fixed dataset size (``make_resize``), which changes the add/remove-one
    adjacency semantics this comparison relies on elsewhere. Forcing it in
    would not be a clean match, so it is left out and noted instead.
    """
    result: dict = {
        "opendp_note": (
            "skipped: opendp's mean transformation requires a fixed dataset size "
            "(make_resize), which does not correspond to the add/remove-one "
            "adjacency used everywhere else in this comparison"
        )
    }
    try:
        diffprivlib = _load_diffprivlib()
        import numpy as np

        array = np.array(amounts, dtype=float)
        errors = [
            abs(float(diffprivlib.tools.mean(array, epsilon=epsilon, bounds=(lower, upper))) - true_mean)
            for _ in range(repetitions)
        ]
        result["diffprivlib"] = summarize_errors(errors)
    except Exception as error:  # pragma: no cover - environment-dependent
        result["diffprivlib_error"] = str(error)

    return result


def _build_reference_entry(
    query_type: str, epsilon: float, rows: int, ground_truth: dict, repetitions: int, our_summary: dict
) -> dict:
    entry = {"query_type": query_type, "epsilon": epsilon, "row_count": rows, "ours": our_summary}
    if query_type == "count":
        entry.update(_reference_count(epsilon, ground_truth["row_count"], repetitions))
    elif query_type == "bounded_sum":
        entry.update(
            _reference_bounded_sum(
                epsilon, AMOUNT_LOWER, AMOUNT_UPPER, ground_truth["sum"], ground_truth["amounts"], repetitions
            )
        )
    elif query_type == "mean":
        entry.update(
            _reference_mean(
                epsilon, AMOUNT_LOWER, AMOUNT_UPPER, ground_truth["mean"], ground_truth["amounts"], repetitions
            )
        )
    return entry


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in ("veil-dp-core", "veil-query-ir", "duckdb", "pyarrow", "diffprivlib", "opendp"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def build_report(
    epsilons: list[float],
    *,
    query_suite: str = "standard",
    row_counts: Sequence[int] = DEFAULT_ROW_COUNTS,
    repetitions: int = DEFAULT_REPETITIONS,
    dataset: Path | None = None,
    seed: int = DEFAULT_SEED,
    include_reference: bool = True,
) -> dict:
    """Run every query in ``query_suite`` at every epsilon, against every dataset size.

    If ``dataset`` is given, it is used as-is (a single dataset "size" equal
    to its actual row count) and ``row_counts``/``seed`` are ignored.
    Otherwise a synthetic Parquet file is generated per entry in
    ``row_counts``.
    """
    if query_suite not in QUERY_SUITES:
        raise ValueError(f"Unknown query suite {query_suite!r}")
    suite = QUERY_SUITES[query_suite]

    started = time.perf_counter()
    dataset_results = []

    with tempfile.TemporaryDirectory(prefix="veil-benchmark-") as tmp:
        if dataset is not None:
            con = duckdb.connect()
            try:
                rows = con.execute("select count(*) from read_parquet(?)", [str(dataset)]).fetchone()[0]
            finally:
                con.close()
            targets = [(dataset, rows)]
        else:
            targets = []
            for rows in row_counts:
                path = Path(tmp) / f"synthetic-{rows}.parquet"
                synthesize_dataset(path, rows, seed)
                targets.append((path, rows))

        for path, rows in targets:
            ground_truth = compute_ground_truth(path)
            query_results = []
            reference_results = []

            for config in suite:
                for epsilon in epsilons:
                    responses, seconds, peak_memory = run_query_repeats(path, config, epsilon, repetitions)
                    errors = [_response_error(config.query_type, response, ground_truth) for response in responses]
                    summary = summarize_errors(errors)
                    query_results.append(
                        {
                            "query_type": config.query_type,
                            "epsilon": epsilon,
                            "epsilon_spent": epsilon,
                            "repetitions": repetitions,
                            "row_count": rows,
                            "group_count": config.group_count,
                            "runtime_seconds": round(seconds, 6),
                            "queries_per_second": round(repetitions / seconds, 2) if seconds else None,
                            "peak_memory_bytes": peak_memory,
                            "mechanism": responses[0].get("mechanism", "laplace") if responses else "laplace",
                            **summary,
                        }
                    )

                    if include_reference and config.query_type in REFERENCE_QUERY_TYPES:
                        reference_results.append(
                            _build_reference_entry(
                                config.query_type, epsilon, rows, ground_truth, repetitions, summary
                            )
                        )

            dataset_results.append(
                {
                    "row_count": rows,
                    "dataset": str(path),
                    "results": query_results,
                    "reference_comparison": reference_results,
                }
            )

    return {
        "query_suite": query_suite,
        "epsilons": epsilons,
        "repetitions": repetitions,
        "runtime_seconds": round(time.perf_counter() - started, 6),
        "python": sys.version,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "package_versions": _package_versions(),
        "datasets": dataset_results,
    }


def _ascii_bar(value: float, max_value: float, width: int = 30) -> str:
    if max_value <= 0 or value <= 0:
        return ""
    filled = max(1, round(width * min(1.0, value / max_value)))
    return "#" * filled


def markdown_report(report: dict) -> str:
    lines = [
        "# Privacy Benchmark Report",
        "",
        f"- **Query suite:** `{report['query_suite']}`",
        f"- **Epsilon values:** {', '.join(str(value) for value in report['epsilons'])}",
        f"- **Repetitions per point:** {report['repetitions']}",
        f"- **Total runtime:** {report['runtime_seconds']:.3f} seconds",
        f"- **Python:** `{report['python'].splitlines()[0]}`",
        f"- **Platform:** `{report['hardware']['platform']}`",
        f"- **CPU count:** {report['hardware']['cpu_count']}",
        "",
        "## Package versions",
        "",
    ]
    for name, version in report["package_versions"].items():
        lines.append(f"- `{name}`: {version or 'not installed'}")
    lines.append("")

    for dataset in report["datasets"]:
        lines.append(f"## Dataset: {dataset['row_count']} rows")
        lines.append("")
        lines.append(
            "| Query | Epsilon | Group count | Mean abs error | P50 | P90 | P99 "
            "| Runtime (s) | Peak mem (KB) | Queries/s | Mechanism |"
        )
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for row in dataset["results"]:
            group_count = row["group_count"] if row["group_count"] is not None else "-"
            qps = row["queries_per_second"] if row["queries_per_second"] is not None else "-"
            lines.append(
                f"| {row['query_type']} | {row['epsilon']:g} | {group_count} "
                f"| {row['mean_absolute_error']} | {row['error_p50']} | {row['error_p90']} | {row['error_p99']} "
                f"| {row['runtime_seconds']:.4f} | {row['peak_memory_bytes'] / 1024:.1f} "
                f"| {qps} | {row['mechanism']} |"
            )
        lines.append("")

        by_query: dict[str, list[tuple[float, float]]] = {}
        for row in dataset["results"]:
            by_query.setdefault(row["query_type"], []).append((row["epsilon"], row["mean_absolute_error"]))
        lines.append("### Mean absolute error by epsilon")
        lines.append("")
        lines.append("```")
        for query_type, points in by_query.items():
            lines.append(f"{query_type}:")
            max_error = max(value for _, value in points)
            for epsilon, value in points:
                bar = _ascii_bar(value, max_error)
                lines.append(f"  eps={epsilon:<5g} {value:>10.3f} |{bar}")
            lines.append("")
        lines.append("```")
        lines.append("")

        if dataset["reference_comparison"]:
            lines.append("### Reference-implementation comparison (matched epsilon and sensitivity)")
            lines.append("")
            lines.append("| Query | Epsilon | Ours (mean err) | diffprivlib (mean err) | vs ours | opendp (mean err) | vs ours |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for entry in dataset["reference_comparison"]:
                ours = entry["ours"]["mean_absolute_error"]
                dpl_summary = entry.get("diffprivlib")
                dpl = dpl_summary["mean_absolute_error"] if dpl_summary else None
                dpl_cell = dpl if dpl is not None else entry.get("diffprivlib_error", "n/a")
                dpl_delta = f"{(dpl - ours) / ours * 100:+.1f}%" if dpl is not None and ours else "-"

                opendp_summary = entry.get("opendp")
                odp = opendp_summary["mean_absolute_error"] if opendp_summary else None
                odp_cell = odp if odp is not None else entry.get("opendp_error", entry.get("opendp_note", "n/a"))
                odp_delta = f"{(odp - ours) / ours * 100:+.1f}%" if odp is not None and ours else "-"

                lines.append(
                    f"| {entry['query_type']} | {entry['epsilon']:g} | {ours} "
                    f"| {dpl_cell} | {dpl_delta} | {odp_cell} | {odp_delta} |"
                )
            lines.append("")

    lines.append("Lower epsilon provides stronger privacy and generally introduces more noise.")
    lines.append("")
    lines.append(
        "**Reference comparison methodology.** diffprivlib and opendp are each given the "
        "same epsilon and the same sensitivity as our own mechanism (1 for count, "
        "max(|lower|, |upper|) for bounded_sum) and error is measured against the "
        "same ground truth over independent repetitions, not a single draw. The mean "
        "query compares against diffprivlib's `tools.mean`, which manages its own "
        "internal epsilon split and so is not expected to match our split-epsilon mean "
        "mechanism's error distribution bit-for-bit even when both are correctly "
        "calibrated. opendp's mean is skipped entirely -- see the note in that row -- "
        "rather than forced into a comparison that would not be apples-to-apples. "
        "grouped_count, histogram, and top_k have no reference column because neither "
        "library exposes a matching public-domain-plus-suppression release."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark private query outputs across query types, epsilon values, and "
            "dataset sizes, with a diffprivlib/opendp reference comparison."
        )
    )
    parser.add_argument("--query-suite", default="standard", choices=sorted(QUERY_SUITES))
    parser.add_argument("--epsilon", default=",".join(str(value) for value in DEFAULT_EPSILONS), type=parse_epsilons)
    parser.add_argument("--output", type=Path, default=Path("benchmark-report"))
    parser.add_argument(
        "--markdown-output", type=Path, help="Markdown report path (defaults to the JSON path with a .md suffix)."
    )
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument(
        "--rows",
        default=",".join(str(value) for value in DEFAULT_ROW_COUNTS),
        type=parse_row_counts,
        help="Comma-separated synthetic dataset sizes to sweep. Ignored if --dataset is given.",
    )
    parser.add_argument("--dataset", type=Path, help="Use an existing Parquet file instead of a synthetic dataset.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed for synthetic dataset generation.")
    parser.add_argument(
        "--no-reference", action="store_true", help="Skip the diffprivlib/opendp reference comparison."
    )
    args = parser.parse_args()

    if args.repetitions < 1:
        parser.error("--repetitions must be positive")

    report = build_report(
        args.epsilon,
        query_suite=args.query_suite,
        row_counts=args.rows,
        repetitions=args.repetitions,
        dataset=args.dataset,
        seed=args.seed,
        include_reference=not args.no_reference,
    )

    json_output = args.output if args.output.suffix else args.output.with_suffix(".json")
    json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_output = args.markdown_output or json_output.with_suffix(".md")
    markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(json_output)
    print(markdown_output)


if __name__ == "__main__":
    main()
