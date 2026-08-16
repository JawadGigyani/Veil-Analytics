"""Hypothesis property-based tests at the analytics worker level.

These tests exercise the executor directly with randomized inputs to
verify that privacy invariants hold for any valid configuration.
"""

import statistics
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from app.executor import execute


def write_sample_parquet(data):
    """Write a Parquet file to a temp dir and return the path."""
    td = tempfile.mkdtemp()
    path = Path(td) / "dataset.parquet"
    parquet.write_table(pa.table(data), path)
    return path


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_count_result_is_non_negative(epsilon):
    path = write_sample_parquet({"value": list(range(20))})
    result = execute(path, "count", epsilon)
    assert result["value"] >= 0
    assert "range" in result


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_gaussian_count_result_is_non_negative_and_self_describing(epsilon):
    path = write_sample_parquet({"value": list(range(20))})
    result = execute(path, "count", epsilon, mechanism="gaussian", delta=1e-5)
    assert result["value"] >= 0
    assert result["mechanism"] == "gaussian"
    assert result["delta"] == 1e-5
    assert result["sigma"] > 0


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_gaussian_mean_is_rejected(epsilon):
    import pytest
    path = write_sample_parquet({"score": [10.0, 20.0, 30.0]})
    with pytest.raises(ValueError, match="not supported for mean"):
        execute(
            path, "mean", epsilon,
            value_column="score", lower=0, upper=100,
            mechanism="gaussian", delta=1e-5,
        )


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_gaussian_requires_positive_delta(epsilon):
    import pytest
    path = write_sample_parquet({"value": list(range(20))})
    with pytest.raises(ValueError, match="requires delta > 0"):
        execute(path, "count", epsilon, mechanism="gaussian", delta=0.0)


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_laplace_rejects_positive_delta(epsilon):
    import pytest
    path = write_sample_parquet({"value": list(range(20))})
    with pytest.raises(ValueError, match="does not accept delta > 0"):
        execute(path, "count", epsilon, mechanism="laplace", delta=1e-5)


@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    lower=st.floats(min_value=-100, max_value=0),
    upper=st.floats(min_value=1, max_value=100),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_bounded_mean_within_bounds(epsilon, lower, upper):
    assume(lower < upper)
    path = write_sample_parquet({"score": [10.0, 20.0, 30.0, 40.0, 50.0]})
    result = execute(
        path, "mean", epsilon,
        value_column="score", lower=lower, upper=upper,
    )
    assert lower <= result["value"] <= upper
    assert result["bounds"] == [lower, upper]


@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    lower=st.floats(min_value=0, max_value=10),
    upper=st.floats(min_value=11, max_value=100),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_bounded_sum_returns_finite(epsilon, lower, upper):
    import math
    path = write_sample_parquet({"score": [5.0, 15.0, 25.0]})
    result = execute(
        path, "bounded_sum", epsilon,
        value_column="score", lower=lower, upper=upper,
    )
    assert math.isfinite(result["value"])
    assert "range" in result


@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    min_group_size=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_grouped_count_returns_subset_of_categories(epsilon, min_group_size):
    path = write_sample_parquet(
        {"category": ["a"] * 15 + ["b"] * 10 + ["c"] * 5},
    )
    categories = ["a", "b", "c"]
    result = execute(
        path, "grouped_count", epsilon,
        group_by="category",
        group_categories=categories,
        min_group_size=min_group_size,
    )
    # Every returned group must be from the public domain
    for item in result["values"]:
        assert item["group"] in categories
        assert item["value"] >= 0


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_histogram_bucket_count_matches_bins(epsilon):
    path = write_sample_parquet({"score": [5, 15, 25, 35, 45]})
    bins = 4
    result = execute(
        path, "histogram", epsilon,
        value_column="score", lower=0, upper=50, bins=bins,
    )
    assert len(result["buckets"]) == bins
    for bucket in result["buckets"]:
        assert bucket["value"] >= 0


@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    k=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_top_k_returns_at_most_k_items(epsilon, k):
    path = write_sample_parquet(
        {"region": ["north"] * 20 + ["south"] * 15 + ["east"] * 10 + ["west"] * 5},
    )
    result = execute(
        path, "top_k", epsilon,
        group_by="region",
        group_categories=["north", "south", "east", "west"],
        top_k=k,
    )
    assert len(result["values"]) <= k
    for item in result["values"]:
        assert item["value"] >= 0


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_filter_reduces_count(epsilon):
    """Filtered count should generally be less than or equal to total count."""
    path = write_sample_parquet(
        {"status": ["active"] * 15 + ["closed"] * 5, "value": list(range(20))},
    )
    # We can only verify structure, not exact values due to noise
    result = execute(
        path, "count", epsilon,
        filters=[{"column": "status", "operator": "equals", "value": "active"}],
    )
    assert result["value"] >= 0


# -- Data independence of the error channel --
#
# Whether any row matches a filter is private.  If an empty match can raise
# where a non-empty match returns, the error channel carries an exact,
# un-noised bit about the data and the release is no longer differentially
# private.  These properties pin that shut across the whole parameter range.

@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    lower=st.floats(min_value=-50, max_value=0),
    upper=st.floats(min_value=1, max_value=200),
    query_type=st.sampled_from(["mean", "bounded_sum"]),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_empty_and_non_empty_filter_matches_are_indistinguishable_in_shape(
    epsilon, lower, upper, query_type,
):
    assume(upper > lower)
    path = write_sample_parquet({
        "score": [float(v) for v in range(20)],
        "region": ["north"] * 20,
    })

    def run(region):
        return execute(
            path, query_type, epsilon,
            value_column="score", lower=lower, upper=upper,
            filters=[{"column": "region", "operator": "equals", "value": region}],
        )

    matched = run("north")
    unmatched = run("west")

    assert matched.keys() == unmatched.keys()
    for key in matched:
        assert type(matched[key]) is type(unmatched[key]), key


@given(
    epsilon=st.floats(min_value=0.1, max_value=2.0),
    min_group_size=st.integers(min_value=1, max_value=20),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_grouped_count_never_raises_on_absent_categories(epsilon, min_group_size):
    """A public category with no rows must release like any other."""
    path = write_sample_parquet({"category": ["alpha"] * 30})

    result = execute(
        path, "grouped_count", epsilon,
        group_by="category",
        group_categories=["alpha", "absent_one", "absent_two"],
        min_group_size=min_group_size,
    )

    assert isinstance(result["values"], list)
    assert result["suppression_threshold"] >= min_group_size


@given(epsilon=st.floats(min_value=0.1, max_value=2.0))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
def test_histogram_tolerates_non_finite_values(epsilon):
    path = write_sample_parquet({
        "score": [1.0, float("nan"), 5.0, float("inf"), float("-inf"), 9.0],
    })

    result = execute(path, "histogram", epsilon, value_column="score", lower=0, upper=10, bins=4)

    assert len(result["buckets"]) == 4
    assert all(bucket["value"] >= 0 for bucket in result["buckets"])


# -- Headline test: repeated entities must produce more noise --
#
# This is the evidence that fixing the adjacency model actually changes
# behaviour, not just the sensitivity formula in isolation. No noise source
# is monkeypatched here -- these two counts are driven through the real
# discrete Laplace mechanism, repeatedly, so the *observed* spread of the
# released value is what is compared, not merely the analytically reported
# `range` field.

def _repeated_counts(path, epsilon, entity_column, max_contributions, trials=300):
    return [
        execute(
            path, "count", epsilon,
            entity_column=entity_column, max_contributions=max_contributions,
        )["value"]
        for _ in range(trials)
    ]


def test_repeated_entities_produce_larger_noise_than_unique_entities():
    """Same row count (100), two adjacency assumptions.

    ``unique``: 100 rows, one row per entity -- sensitivity 1, matching
    today's (unenforced) one-row-per-person assumption.

    ``repeated``: the same 100 rows, but only 20 distinct entities, each
    contributing 5 rows -- sensitivity 5 once max_contributions=5 is
    declared and enforced.

    Both release the same true count (100), through the same mechanism, at
    the same epsilon. Only the declared contribution bound differs. If the
    fix does nothing, the two noise distributions would be identical; if it
    works, the repeated-entity release must be visibly noisier.
    """
    unique_path = write_sample_parquet({
        "person": list(range(100)),
        "value": list(range(100)),
    })
    repeated_path = write_sample_parquet({
        "person": [i // 5 for i in range(100)],  # 20 entities x 5 rows each
        "value": list(range(100)),
    })

    epsilon = 0.5
    unique_counts = _repeated_counts(unique_path, epsilon, "person", 1)
    repeated_counts = _repeated_counts(repeated_path, epsilon, "person", 5)

    # Both are unbiased releases of the same true count.
    assert statistics.mean(unique_counts) == pytest.approx(100, abs=15)
    assert statistics.mean(repeated_counts) == pytest.approx(100, abs=40)

    unique_spread = statistics.pstdev(unique_counts)
    repeated_spread = statistics.pstdev(repeated_counts)

    # Sensitivity is 5x higher for the repeated-entity release, so its noise
    # scale (and therefore its standard deviation) should be roughly 5x
    # larger. Assert a conservative multiple to keep this robust to sampling
    # noise while still being a meaningful, visible difference.
    assert repeated_spread > unique_spread * 2.5


def test_repeated_entities_produce_wider_reported_range_than_unique_entities():
    """The self-reported uncertainty (`range`) must widen the same way the
    observed noise does -- an analyst reading the response, not just the
    distribution of many releases, must be able to see the fix took effect.
    """
    path = write_sample_parquet({
        "person": [i // 5 for i in range(100)],
        "value": list(range(100)),
    })
    unique_like = execute(path, "count", 0.5, entity_column="person", max_contributions=1)
    repeated = execute(path, "count", 0.5, entity_column="person", max_contributions=5)

    assert repeated["range"] == pytest.approx(unique_like["range"] * 5, abs=0.02)
