"""Tests for query validation."""

import pytest

from query_ir.models import ColumnSchema, DatasetSchema, PolicySpec, QuerySpec
from query_ir.validator import ValidationError, estimate_privacy_cost, estimate_sensitivity, validate_query


def make_schema():
    return DatasetSchema(columns=[
        ColumnSchema(name="age", data_type="int", can_measure=True, lower_bound=0, upper_bound=100),
        ColumnSchema(name="region", data_type="text", can_group=True),
        ColumnSchema(name="score", data_type="float", can_measure=True, lower_bound=0, upper_bound=50),
        ColumnSchema(name="unbounded", data_type="float", can_measure=False),
        ColumnSchema(
            name="diagnosis", data_type="text", can_group=True, can_measure=True,
            lower_bound=0, upper_bound=1, is_sensitive=True,
        ),
    ])


def make_policy(**overrides):
    defaults = dict(
        epsilon_total=5.0, epsilon_used=0.0, delta_total=1e-6,
        allowed_query_types=["count", "grouped_count", "bounded_sum", "mean", "histogram", "top_k"],
        public_categories={"region": ["north", "south", "east", "west"]},
    )
    defaults.update(overrides)
    return PolicySpec(**defaults)


class TestValidateQuery:
    def test_valid_count(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        validate_query(spec, make_schema(), make_policy())  # no exception

    def test_unknown_column_rejected(self):
        spec = QuerySpec(query_type="count", epsilon=1.0, filters=[
            {"column": "nonexistent", "operator": "equals", "value": "x"}
        ])
        with pytest.raises(ValidationError, match="Unknown columns"):
            validate_query(spec, make_schema(), make_policy())

    def test_non_groupable_column_rejected(self):
        spec = QuerySpec(query_type="grouped_count", group_by=["age"], epsilon=1.0)
        with pytest.raises(ValidationError, match="cannot be used for grouping"):
            validate_query(spec, make_schema(), make_policy())

    def test_unbounded_value_column_rejected(self):
        spec = QuerySpec(query_type="mean", value_column="unbounded", epsilon=1.0)
        with pytest.raises(ValidationError, match="cannot be used for measurement"):
            validate_query(spec, make_schema(), make_policy())

    def test_budget_overflow_rejected(self):
        spec = QuerySpec(query_type="count", epsilon=4.0)
        policy = make_policy(epsilon_used=3.0)
        with pytest.raises(ValidationError, match="Insufficient privacy budget"):
            validate_query(spec, make_schema(), policy)

    def test_disallowed_query_type_rejected(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        policy = make_policy(allowed_query_types=["mean"])
        with pytest.raises(ValidationError, match="not allowed"):
            validate_query(spec, make_schema(), policy)

    def test_grouped_without_public_categories_rejected(self):
        spec = QuerySpec(query_type="grouped_count", group_by=["region"], epsilon=1.0)
        policy = make_policy(public_categories={})
        with pytest.raises(ValidationError, match="public category domain"):
            validate_query(spec, make_schema(), policy)

    def test_valid_mean(self):
        spec = QuerySpec(query_type="mean", value_column="age", epsilon=0.5)
        validate_query(spec, make_schema(), make_policy())

    def test_unknown_entity_column_rejected(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        policy = make_policy(entity_column="person_id")
        with pytest.raises(ValidationError, match="[Uu]nknown entity column"):
            validate_query(spec, make_schema(), policy)

    def test_known_entity_column_accepted(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        policy = make_policy(entity_column="region", max_contributions=5)
        validate_query(spec, make_schema(), policy)  # no exception

    def test_no_entity_column_is_unaffected(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        validate_query(spec, make_schema(), make_policy())  # no exception

    def test_sensitive_column_rejected_for_group_by(self):
        spec = QuerySpec(query_type="grouped_count", group_by=["diagnosis"], epsilon=1.0)
        policy = make_policy(public_categories={"diagnosis": ["flu", "none"]})
        with pytest.raises(ValidationError, match="sensitive"):
            validate_query(spec, make_schema(), policy)

    def test_sensitive_column_rejected_for_top_k_group_by(self):
        spec = QuerySpec(query_type="top_k", group_by=["diagnosis"], epsilon=1.0)
        policy = make_policy(public_categories={"diagnosis": ["flu", "none"]})
        with pytest.raises(ValidationError, match="sensitive"):
            validate_query(spec, make_schema(), policy)

    def test_sensitive_column_rejected_as_equals_filter(self):
        spec = QuerySpec(query_type="count", epsilon=1.0, filters=[
            {"column": "diagnosis", "operator": "equals", "value": "flu"}
        ])
        with pytest.raises(ValidationError, match="sensitive"):
            validate_query(spec, make_schema(), make_policy())

    def test_sensitive_column_rejected_as_not_equals_filter(self):
        spec = QuerySpec(query_type="count", epsilon=1.0, filters=[
            {"column": "diagnosis", "operator": "not_equals", "value": "flu"}
        ])
        with pytest.raises(ValidationError, match="sensitive"):
            validate_query(spec, make_schema(), make_policy())

    def test_sensitive_column_accepted_as_measured_bounded_sum_column(self):
        spec = QuerySpec(query_type="bounded_sum", value_column="diagnosis", epsilon=1.0)
        validate_query(spec, make_schema(), make_policy())  # no exception

    def test_sensitive_column_accepted_as_measured_mean_column(self):
        spec = QuerySpec(query_type="mean", value_column="diagnosis", epsilon=1.0)
        validate_query(spec, make_schema(), make_policy())  # no exception

    def test_non_sensitive_column_unaffected_by_sensitivity_check(self):
        spec = QuerySpec(query_type="grouped_count", group_by=["region"], epsilon=1.0)
        validate_query(spec, make_schema(), make_policy())  # no exception


class TestEstimateSensitivity:
    def test_count_sensitivity(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        assert estimate_sensitivity(spec, make_schema()) == 1.0

    def test_sum_sensitivity(self):
        spec = QuerySpec(query_type="bounded_sum", value_column="age", epsilon=1.0)
        assert estimate_sensitivity(spec, make_schema()) == 100.0

    def test_mean_sensitivity(self):
        spec = QuerySpec(query_type="mean", value_column="score", epsilon=1.0)
        assert estimate_sensitivity(spec, make_schema()) == 50.0


class TestEstimatePrivacyCost:
    def test_laplace_cost(self):
        spec = QuerySpec(query_type="count", epsilon=0.5)
        eps, delta = estimate_privacy_cost(spec)
        assert eps == 0.5
        assert delta == 0.0

    def test_gaussian_cost(self):
        spec = QuerySpec(query_type="count", epsilon=0.5, mechanism="gaussian", delta=1e-5)
        eps, delta = estimate_privacy_cost(spec)
        assert eps == 0.5
        assert delta == 1e-5
