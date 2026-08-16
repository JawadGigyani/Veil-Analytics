import pyarrow as pa
import pyarrow.parquet as parquet
import pytest

from app import privacy
from app.executor import execute
from dp_core.mechanisms import discrete as dp_discrete
from dp_core.mechanisms import gaussian as dp_gaussian
from dp_core.mechanisms import laplace as dp_laplace


@pytest.fixture(autouse=True)
def no_privacy_noise(monkeypatch):
    """Patch every noise source so releases are deterministically un-noised.

    Counts, grouped counts, and histogram buckets go through the discrete
    (geometric) mechanism; real-valued sums and means still go through the
    float Laplace path; Gaussian releases go through ``gaussian_noise``.
    All three have to be silenced or the exact-value assertions below become
    flaky. Patching ``dp_gaussian.gaussian_noise`` (the module attribute)
    zeroes noise both for the *_analytic release functions defined inside
    that module and for executor.py's own direct calls to
    ``dp_gaussian.gaussian_noise`` in the grouped-selection path, because
    both resolve the name through the same module object at call time.
    """
    monkeypatch.setattr(dp_laplace, "laplace_noise", lambda scale: 0.0)
    monkeypatch.setattr(privacy, "laplace_noise", lambda scale: 0.0)
    monkeypatch.setattr(privacy, "laplace", lambda scale: 0.0)
    monkeypatch.setattr(dp_discrete, "discrete_laplace_noise", lambda scale: 0)
    monkeypatch.setattr(privacy, "discrete_laplace", lambda scale: 0)
    monkeypatch.setattr(dp_gaussian, "gaussian_noise", lambda scale: 0.0)


def write_parquet(tmp_path, data):
    path = tmp_path / "dataset.parquet"
    parquet.write_table(pa.table(data), path)
    return path


def test_count_executes_against_parquet(tmp_path):
    path = write_parquet(tmp_path, {"value": [1, 2, 3, 4, 5, 6, 7]})

    assert execute(path, "count", epsilon=1.0) == {
        "value": 7,
        "range": 3.0,
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_grouped_count_uses_noisy_selection_without_leaking_suppressed_count(tmp_path):
    # Selection spends epsilon/2 = 0.5, so the suppression margin is
    # confidence_radius(0.5) = 5.99 and the effective threshold is 10.99.
    path = write_parquet(
        tmp_path,
        {"category": ["alpha"] * 20 + ["beta"] * 15 + ["hidden"] * 4},
    )

    result = execute(path, "grouped_count", epsilon=1.0, group_by="category",group_categories=["alpha","beta","hidden"])

    assert {row["group"]: row["value"] for row in result["values"]} == {
        "alpha": 20,
        "beta": 15,
    }
    assert result["selection"] == "public_domain_noisy_threshold"
    assert result["truncated"] is False
    assert "suppressed_groups" not in result


def test_grouped_count_reports_the_threshold_it_applied(tmp_path):
    path = write_parquet(tmp_path, {"category": ["alpha"] * 20})

    result = execute(path, "grouped_count", 1.0, group_by="category", group_categories=["alpha"])

    # min_group_size + confidence_radius(epsilon / 2) == 5 + 5.99
    assert result["min_group_size"] == 5
    assert result["suppression_threshold"] == 10.99


def test_grouped_count_margin_suppresses_groups_that_only_nominally_clear_the_minimum(tmp_path):
    # Six rows clears min_group_size=5 nominally, but not by the margin that
    # makes the policy floor hold with 95% confidence.
    path = write_parquet(tmp_path, {"category": ["alpha"] * 6})

    result = execute(path, "grouped_count", 1.0, group_by="category", group_categories=["alpha"])

    assert result["values"] == []


def test_filters_apply_to_count_and_group_suppression_is_configurable(tmp_path):
    path = write_parquet(tmp_path, {
        "category": ["alpha"] * 12 + ["beta"] * 3,
        "status": ["active"] * 5 + ["closed"] * 10,
    })

    count = execute(path, "count", 1.0, filters=[{"column":"status", "operator":"equals", "value":"active"}])
    grouped = execute(path, "grouped_count", 1.0, group_by="category", min_group_size=4,group_categories=["alpha","beta"])

    assert count == {
        "value": 5,
        "range": 3.0,
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }
    assert grouped == {
        "values": [{"group": "alpha", "value": 12, "range": 5.99}],
        "selection": "public_domain_noisy_threshold",
        "truncated": False,
        "range": 5.99,
        "suppression_threshold": 9.99,
        "min_group_size": 4,
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_mean_executes_against_parquet_and_uses_requested_bounds(tmp_path):
    path = write_parquet(tmp_path, {"score": [10.0, 20.0, 30.0]})

    result = execute(
        path,
        "mean",
        epsilon=1.0,
        value_column="score",
        lower=0,
        upper=25,
    )

    # ln(20) * (25 - 0) / (0.5 * 10) = 14.98. The radius dwarfs the released
    # value because three records is far below the denominator floor -- the
    # analyst is meant to see that this mean carries no information.
    assert result == {
        "value": 5.5,
        "bounds": [0, 25],
        "minimum_denominator": 10,
        "range": 14.98,
        "range_basis": "sum_noise_only",
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_mean_clips_each_record_before_averaging(tmp_path):
    path = write_parquet(tmp_path, {"score": [-100.0, 20.0, 30.0]})

    result = execute(path, "mean", 1.0, value_column="score", lower=0, upper=30)

    assert result == {
        "value": 5.0,
        "bounds": [0, 30],
        "minimum_denominator": 10,
        "range": 17.97,
        "range_basis": "sum_noise_only",
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_bounded_sum_clips_each_row(tmp_path):
    path = write_parquet(tmp_path, {"score": [-100.0, 20.0, 100.0]})
    assert execute(path, "bounded_sum", 1.0, value_column="score", lower=0, upper=30) == {
        "value": 50.0,
        "bounds": [0, 30],
        "range": 89.87,
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_grouped_count_enforces_max_groups(tmp_path):
    path = write_parquet(tmp_path, {"category": ["a"]*12+["b"]*12+["c"]*12})
    result = execute(path,"grouped_count",1.0,group_by="category",max_groups=2,group_categories=["a","b","c"])
    assert len(result["values"]) == 2
    assert result["truncated"] is True


def test_histogram_assigns_values_to_bins_and_clamps_outliers(tmp_path):
    path = write_parquet(
        tmp_path,
        {"score": [-5.0, 0.0, 9.99, 10.0, 19.99, 20.0, 39.99, 40.0, 100.0]},
    )

    result = execute(
        path,
        "histogram",
        epsilon=1.0,
        value_column="score",
        lower=0,
        upper=40,
        bins=4,
    )

    assert result == {
        "buckets": [
            {"from": 0.0, "to": 10.0, "value": 3, "range": 3.0},
            {"from": 10.0, "to": 20.0, "value": 2, "range": 3.0},
            {"from": 20.0, "to": 30.0, "value": 1, "range": 3.0},
            {"from": 30.0, "to": 40, "value": 3, "range": 3.0},
        ],
        "mechanism": "laplace",
        "entity_column": None,
        "max_contributions": 1,
        "privacy_unit": "row",
    }


def test_empty_filter_match_releases_a_value_instead_of_raising(tmp_path):
    """Whether any row matched is private and must not change the response.

    Before this guard the mean/bounded_sum path raised when the filtered
    result set was empty, handing the analyst an exact, un-noised bit about
    the data through the error channel.
    """
    path = write_parquet(tmp_path, {
        "score": [10.0] * 20,
        "region": ["north"] * 20,
    })

    matched = execute(path, "mean", 1.0, value_column="score", lower=0, upper=100,
                      filters=[{"column": "region", "operator": "equals", "value": "north"}])
    unmatched = execute(path, "mean", 1.0, value_column="score", lower=0, upper=100,
                        filters=[{"column": "region", "operator": "equals", "value": "west"}])

    # Same keys, same types: the empty case is indistinguishable in shape.
    assert matched.keys() == unmatched.keys()
    assert isinstance(unmatched["value"], float)


def test_empty_filter_match_on_bounded_sum_releases_a_value(tmp_path):
    path = write_parquet(tmp_path, {"score": [10.0] * 20, "region": ["north"] * 20})

    result = execute(path, "bounded_sum", 1.0, value_column="score", lower=0, upper=100,
                     filters=[{"column": "region", "operator": "equals", "value": "west"}])

    # Noise is patched to zero, so an empty match sums to exactly 0.0 rather
    # than propagating SQL NULL or raising.
    assert result["value"] == 0.0
    assert result["bounds"] == [0, 100]


def test_histogram_drops_non_finite_values_instead_of_raising(tmp_path):
    """NaN survives the SQL "is not null" filter and would make int() raise."""
    path = write_parquet(tmp_path, {"score": [1.0, float("nan"), 5.0, float("inf")]})

    result = execute(path, "histogram", 1.0, value_column="score", lower=0, upper=10, bins=2)

    # 1.0 lands in [0, 5), 5.0 in [5, 10]; NaN and inf are dropped entirely.
    assert [bucket["value"] for bucket in result["buckets"]] == [1, 1]


class TestRowRestrictions:
    """Owner-defined predicates, AND-ed in ahead of the analyst's own
    filters (see executor.py's module docstring's "Row restrictions"
    section). ``no_privacy_noise`` (module-level autouse fixture) zeroes
    every noise source, so released counts equal the true, restricted count.
    """

    def test_restrictions_filter_roughly_half_the_rows(self, tmp_path):
        path = write_parquet(tmp_path, {
            "value": list(range(20)),
            "included": ["yes"] * 10 + ["no"] * 10,
        })
        result = execute(path, "count", 1.0, row_restrictions=[
            {"column": "included", "operator": "equals", "value": "yes"}
        ])
        assert result["value"] == 10

    def test_restrictions_apply_even_when_analyst_sends_no_filters(self, tmp_path):
        path = write_parquet(tmp_path, {
            "value": list(range(6)),
            "included": ["yes", "no", "yes", "no", "yes", "no"],
        })
        result = execute(path, "count", 1.0, filters=None, row_restrictions=[
            {"column": "included", "operator": "equals", "value": "yes"}
        ])
        assert result["value"] == 3

    def test_not_equals_restriction_excludes_matching_rows(self, tmp_path):
        path = write_parquet(tmp_path, {
            "value": list(range(10)),
            "status": ["closed"] * 4 + ["open"] * 6,
        })
        result = execute(path, "count", 1.0, row_restrictions=[
            {"column": "status", "operator": "not_equals", "value": "closed"}
        ])
        assert result["value"] == 6

    def test_restriction_matching_zero_rows_releases_a_value_not_an_error(self, tmp_path):
        path = write_parquet(tmp_path, {"value": [1, 2, 3], "included": ["no"] * 3})
        result = execute(path, "count", 1.0, row_restrictions=[
            {"column": "included", "operator": "equals", "value": "yes"}
        ])
        assert result["value"] == 0

    def test_restriction_combines_with_analyst_filters(self, tmp_path):
        path = write_parquet(tmp_path, {
            "included": ["yes"] * 10 + ["no"] * 10,
            "region": ["north"] * 5 + ["south"] * 5 + ["north"] * 10,
        })
        result = execute(
            path, "count", 1.0,
            row_restrictions=[{"column": "included", "operator": "equals", "value": "yes"}],
            filters=[{"column": "region", "operator": "equals", "value": "north"}],
        )
        assert result["value"] == 5

    def test_restriction_applies_to_grouped_count(self, tmp_path):
        # Counts well above the suppression threshold (min_group_size=5 +
        # confidence_radius(selection_epsilon=0.5) ~= 10.99) so the
        # restriction's effect on the released count is what's under test,
        # not incidental suppression.
        path = write_parquet(tmp_path, {
            "category": ["alpha"] * 40 + ["beta"] * 30,
            "included": ["yes"] * 30 + ["no"] * 10 + ["yes"] * 30,
        })
        result = execute(
            path, "grouped_count", 1.0, group_by="category",
            group_categories=["alpha", "beta"],
            row_restrictions=[{"column": "included", "operator": "equals", "value": "yes"}],
        )
        assert {row["group"]: row["value"] for row in result["values"]} == {
            "alpha": 30, "beta": 30,
        }

    def test_restriction_referencing_column_absent_from_file_raises_static_error(self, tmp_path):
        path = write_parquet(tmp_path, {"value": [1, 2, 3]})
        with pytest.raises(ValueError) as exc_info:
            execute(path, "count", 1.0, row_restrictions=[
                {"column": "ssn", "operator": "equals", "value": "123-45-6789"}
            ])
        message = str(exc_info.value)
        assert "ssn" not in message
        assert "123-45-6789" not in message

    def test_empty_restrictions_reproduce_existing_behaviour_exactly(self, tmp_path):
        path = write_parquet(tmp_path, {"value": [1, 2, 3, 4, 5]})
        with_empty_list = execute(path, "count", 1.0, row_restrictions=[])
        with_none = execute(path, "count", 1.0, row_restrictions=None)
        without_param = execute(path, "count", 1.0)
        assert with_empty_list == with_none == without_param

    def test_empty_restrictions_reproduce_existing_behaviour_for_mean(self, tmp_path):
        path = write_parquet(tmp_path, {"score": [10.0, 20.0, 30.0]})
        with_empty = execute(
            path, "mean", 1.0, value_column="score", lower=0, upper=25, row_restrictions=[],
        )
        without_param = execute(path, "mean", 1.0, value_column="score", lower=0, upper=25)
        assert with_empty == without_param


def test_incomplete_or_unknown_query_is_rejected(tmp_path):
    path = write_parquet(tmp_path, {"value": [1]})

    with pytest.raises(ValueError, match="Unsupported or incomplete query"):
        execute(path, "mean", epsilon=1.0)


def test_gaussian_mean_is_rejected(tmp_path):
    """Gaussian is refused for mean specifically -- not gaussian as a whole.

    The bounded mean already splits epsilon between a noisy sum and a noisy
    count; splitting delta across both as well is a design decision that
    needs its own justification, and guessing at it would be worse than
    refusing.
    """
    path = write_parquet(tmp_path, {"score": [10.0, 20.0, 30.0]})
    with pytest.raises(ValueError, match="not supported for mean"):
        execute(
            path,
            "mean",
            1.0,
            value_column="score",
            lower=0,
            upper=30,
            mechanism="gaussian",
            delta=1e-5,
        )


def test_gaussian_with_zero_delta_is_rejected(tmp_path):
    path = write_parquet(tmp_path, {"value": [1, 2, 3]})
    with pytest.raises(ValueError, match="requires delta > 0"):
        execute(path, "count", 1.0, mechanism="gaussian", delta=0.0)


def test_gaussian_with_negative_delta_is_rejected(tmp_path):
    path = write_parquet(tmp_path, {"value": [1, 2, 3]})
    with pytest.raises(ValueError, match="requires delta > 0"):
        execute(path, "count", 1.0, mechanism="gaussian", delta=-1e-5)


def test_laplace_with_positive_delta_is_rejected(tmp_path):
    path = write_parquet(tmp_path, {"value": [1, 2, 3]})
    with pytest.raises(ValueError, match="does not accept delta > 0"):
        execute(path, "count", 1.0, mechanism="laplace", delta=1e-5)


class TestGaussianReleases:
    """Gaussian releases for the supported query types: count, grouped_count,
    top_k, histogram, bounded_sum. Every release must be self-describing
    (mechanism, delta, sigma) and sigma must provably come from
    ``calibrate_sigma_analytic`` -- never the classical, epsilon<=1-limited
    ``calibrate_sigma``.

    ``no_privacy_noise`` zeroes ``gaussian_noise``, so released values equal
    the true (unnoised) aggregate; only ``sigma``/``range`` reflect the
    calibration.
    """

    def test_count_is_self_describing(self, tmp_path):
        path = write_parquet(tmp_path, {"value": [1, 2, 3, 4, 5]})
        result = execute(path, "count", 1.0, mechanism="gaussian", delta=1e-5)
        assert result["value"] == 5
        assert result["mechanism"] == "gaussian"
        assert result["delta"] == 1e-5
        assert result["sigma"] == dp_gaussian.calibrate_sigma_analytic(1.0, 1.0, 1e-5)
        assert result["range"] > 0

    def test_count_sigma_valid_above_epsilon_one(self, tmp_path):
        """epsilon=2 is where the classical calibrate_sigma would refuse
        outright -- the Gaussian release path must still work."""
        path = write_parquet(tmp_path, {"value": [1, 2, 3]})
        with pytest.raises(ValueError, match="only valid for epsilon"):
            dp_gaussian.calibrate_sigma(1.0, 2.0, 1e-5)
        result = execute(path, "count", 2.0, mechanism="gaussian", delta=1e-5)
        assert result["sigma"] == dp_gaussian.calibrate_sigma_analytic(1.0, 2.0, 1e-5)

    def test_bounded_sum_is_self_describing(self, tmp_path):
        path = write_parquet(tmp_path, {"score": [10.0, 20.0, 100.0]})
        result = execute(
            path, "bounded_sum", 1.0, value_column="score", lower=0, upper=30,
            mechanism="gaussian", delta=1e-5,
        )
        assert result["value"] == 60.0  # 10 + 20 + 30 (clipped)
        assert result["mechanism"] == "gaussian"
        assert result["delta"] == 1e-5
        # sensitivity = max(|lower|, |upper|) = 30
        assert result["sigma"] == dp_gaussian.calibrate_sigma_analytic(30.0, 1.0, 1e-5)

    def test_histogram_is_self_describing(self, tmp_path):
        # step = (20-0)/2 = 10. 1.0,5.0 -> bucket 0; 15.0 -> bucket 1;
        # 25.0 is out of range and clamps to the last bucket (bucket 1).
        path = write_parquet(tmp_path, {"score": [1.0, 5.0, 15.0, 25.0]})
        result = execute(
            path, "histogram", 1.0, value_column="score", lower=0, upper=20, bins=2,
            mechanism="gaussian", delta=1e-5,
        )
        assert result["mechanism"] == "gaussian"
        assert result["delta"] == 1e-5
        assert result["sigma"] == dp_gaussian.calibrate_sigma_analytic(1.0, 1.0, 1e-5)
        assert [b["value"] for b in result["buckets"]] == [2, 2]
        assert all(b["range"] == round(dp_gaussian.gaussian_confidence_radius(result["sigma"]), 2)
                   for b in result["buckets"])

    def test_grouped_count_is_self_describing(self, tmp_path):
        # delta=0.05 keeps the selection-phase suppression margin small
        # enough that both groups clear the threshold under zeroed noise --
        # a tiny delta (e.g. 1e-5) calibrates a much larger sigma and would
        # suppress 'beta' here, which is a real, correct consequence of a
        # thin delta budget rather than a bug in the test.
        path = write_parquet(tmp_path, {"category": ["alpha"] * 20 + ["beta"] * 15})
        result = execute(
            path, "grouped_count", 1.0, group_by="category",
            group_categories=["alpha", "beta"], mechanism="gaussian", delta=0.05,
        )
        assert result["mechanism"] == "gaussian"
        assert result["delta"] == 0.05
        assert result["sigma"] == dp_gaussian.calibrate_sigma_analytic(1.0, 0.5, 0.025)
        assert {row["group"]: row["value"] for row in result["values"]} == {
            "alpha": 20,
            "beta": 15,
        }

    def test_top_k_is_self_describing_and_reuses_grouped_path(self, tmp_path):
        path = write_parquet(tmp_path, {
            "region": ["north"] * 20 + ["south"] * 15 + ["east"] * 10 + ["west"] * 5,
        })
        result = execute(
            path, "top_k", 1.0, group_by="region",
            group_categories=["north", "south", "east", "west"],
            top_k=2, mechanism="gaussian", delta=0.05,
        )
        assert result["mechanism"] == "gaussian"
        assert len(result["values"]) <= 2

    def test_sigma_scales_with_max_contributions_for_count(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1] * 10, "value": list(range(10))})
        k1 = execute(path, "count", 1.0, entity_column="person", max_contributions=1,
                     mechanism="gaussian", delta=1e-5)
        k4 = execute(path, "count", 1.0, entity_column="person", max_contributions=4,
                     mechanism="gaussian", delta=1e-5)
        assert k4["sigma"] == dp_gaussian.calibrate_sigma_analytic(4.0, 1.0, 1e-5)
        assert k4["sigma"] > k1["sigma"]
        assert k4["range"] > k1["range"]

    def test_sigma_scales_with_max_contributions_for_bounded_sum(self, tmp_path):
        path = write_parquet(tmp_path, {
            "person": [1] * 5,
            "value": [10.0, 10.0, 10.0, 10.0, 10.0],
        })
        k1 = execute(
            path, "bounded_sum", 1.0, value_column="value", lower=0, upper=20,
            mechanism="gaussian", delta=1e-5,
        )
        k3 = execute(
            path, "bounded_sum", 1.0, value_column="value", lower=0, upper=20,
            entity_column="person", max_contributions=3, mechanism="gaussian", delta=1e-5,
        )
        assert k3["sigma"] == dp_gaussian.calibrate_sigma_analytic(60.0, 1.0, 1e-5)
        assert k3["sigma"] > k1["sigma"]

    def test_reports_entity_adjacency_metadata_alongside_gaussian_fields(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1, 2, 3], "value": [1, 2, 3]})
        result = execute(
            path, "count", 1.0, entity_column="person", max_contributions=4,
            mechanism="gaussian", delta=1e-5,
        )
        assert result["entity_column"] == "person"
        assert result["max_contributions"] == 4
        assert result["privacy_unit"] == "entity"
        assert result["mechanism"] == "gaussian"


class TestEntityContributionBounding:
    """Contribution bounding under add/remove-one-ENTITY adjacency.

    ``no_privacy_noise`` (module-level autouse fixture) zeroes every noise
    source, so any change in the released *value* below comes from entity
    capping, and any change in the released *range* comes from sensitivity
    scaling -- not from randomness.
    """

    def test_caps_a_single_entitys_contribution(self, tmp_path):
        # One entity contributes 50 rows; capped to 2, the noise-free count
        # must reflect exactly 2, not 50.
        path = write_parquet(tmp_path, {
            "person": [1] * 50,
            "value": list(range(50)),
        })
        result = execute(path, "count", 1.0, entity_column="person", max_contributions=2)
        assert result["value"] == 2

    def test_caps_every_entity_independently(self, tmp_path):
        # person 1 has 50 rows (capped to 2), person 2 has 3 rows (capped to
        # 2), person 3 has 1 row (under the cap, kept whole).
        path = write_parquet(tmp_path, {
            "person": [1] * 50 + [2] * 3 + [3] * 1,
            "value": list(range(54)),
        })
        result = execute(path, "count", 1.0, entity_column="person", max_contributions=2)
        assert result["value"] == 2 + 2 + 1

    def test_result_is_self_describing_when_entity_bounded(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1, 2, 3], "value": [1, 2, 3]})
        result = execute(path, "count", 1.0, entity_column="person", max_contributions=4)
        assert result["entity_column"] == "person"
        assert result["max_contributions"] == 4
        assert result["privacy_unit"] == "entity"

    def test_result_is_self_describing_when_row_adjacency(self, tmp_path):
        path = write_parquet(tmp_path, {"value": [1, 2, 3]})
        result = execute(path, "count", 1.0)
        assert result["entity_column"] is None
        assert result["max_contributions"] == 1
        assert result["privacy_unit"] == "row"

    def test_entity_column_none_ignores_max_contributions(self, tmp_path):
        """Without an entity column there is nothing to bound contribution
        by, so max_contributions must not silently change the release."""
        path = write_parquet(tmp_path, {"value": list(range(20))})
        default = execute(path, "count", 1.0)
        explicit = execute(path, "count", 1.0, entity_column=None, max_contributions=1)
        ignored = execute(path, "count", 1.0, entity_column=None, max_contributions=50)
        assert default == explicit == ignored

    def test_max_contributions_one_with_entity_column_reproduces_uncapped_count(self, tmp_path):
        """k=1 with an entity column still caps at 1 row/entity -- unlike
        entity_column=None, this is a real (and different) constraint when
        an entity has more than one row."""
        path = write_parquet(tmp_path, {"person": [1, 1, 2], "value": [1, 2, 3]})
        bounded = execute(path, "count", 1.0, entity_column="person", max_contributions=1)
        assert bounded["value"] == 2  # person 1 capped from 2 rows to 1

    def test_bounded_sum_caps_rows_and_scales_sensitivity(self, tmp_path):
        path = write_parquet(tmp_path, {
            "person": [1] * 5,
            "value": [10.0, 10.0, 10.0, 10.0, 10.0],
        })
        capped = execute(
            path, "bounded_sum", 1.0, value_column="value", lower=0, upper=20,
            entity_column="person", max_contributions=2,
        )
        uncapped = execute(path, "bounded_sum", 1.0, value_column="value", lower=0, upper=20)

        # Only 2 of the 5 rows survive capping, each clipped to 10 -> 20.
        assert capped["value"] == 20.0
        assert uncapped["value"] == 50.0
        # Sensitivity (and therefore the reported range) is k * max(|lower|,
        # |upper|) = 2 * 20 = 40, twice the uncapped range (both sides are
        # independently rounded to 2dp, hence the small absolute tolerance).
        assert capped["range"] == pytest.approx(uncapped["range"] * 2, abs=0.02)

    def test_histogram_scales_range_by_max_contributions(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1, 1, 1], "value": [1.0, 2.0, 3.0]})
        bounded = execute(
            path, "histogram", 1.0, value_column="value", lower=0, upper=10, bins=2,
            entity_column="person", max_contributions=3,
        )
        unbounded = execute(path, "histogram", 1.0, value_column="value", lower=0, upper=10, bins=2)
        for b, u in zip(bounded["buckets"], unbounded["buckets"]):
            assert b["range"] == pytest.approx(u["range"] * 3, abs=0.02)

    def test_grouped_count_suppression_threshold_scales_with_k(self, tmp_path):
        path = write_parquet(tmp_path, {
            "person": [1] * 20,
            "category": ["alpha"] * 20,
        })
        bounded = execute(
            path, "grouped_count", 1.0, group_by="category", group_categories=["alpha"],
            entity_column="person", max_contributions=4,
        )
        unbounded = execute(
            path, "grouped_count", 1.0, group_by="category", group_categories=["alpha"],
        )
        # threshold = min_group_size + confidence_radius(selection_epsilon, k)
        assert bounded["suppression_threshold"] > unbounded["suppression_threshold"]

    def test_entity_with_no_matching_rows_releases_like_any_other(self, tmp_path):
        """An entity column with no matching rows must not change the
        response shape -- the empty-filter-match invariant still holds
        under entity bounding."""
        path = write_parquet(tmp_path, {
            "person": [1] * 10,
            "region": ["north"] * 10,
            "score": [10.0] * 10,
        })
        matched = execute(
            path, "mean", 1.0, value_column="score", lower=0, upper=100,
            entity_column="person", max_contributions=2,
            filters=[{"column": "region", "operator": "equals", "value": "north"}],
        )
        unmatched = execute(
            path, "mean", 1.0, value_column="score", lower=0, upper=100,
            entity_column="person", max_contributions=2,
            filters=[{"column": "region", "operator": "equals", "value": "west"}],
        )
        assert matched.keys() == unmatched.keys()
        for key in matched:
            assert type(matched[key]) is type(unmatched[key]), key

    def test_rejects_zero_or_negative_max_contributions(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1], "value": [1]})
        with pytest.raises(ValueError):
            execute(path, "count", 1.0, entity_column="person", max_contributions=0)
