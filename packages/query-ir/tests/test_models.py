"""Tests for query-ir Pydantic models."""

import pytest
from pydantic import ValidationError

from query_ir.models import ColumnSchema, DatasetSchema, FilterSpec, PolicySpec, QuerySpec


class TestQuerySpec:
    def test_valid_count(self):
        q = QuerySpec(query_type="count", epsilon=1.0)
        assert q.query_type == "count"

    def test_valid_grouped_count(self):
        q = QuerySpec(query_type="grouped_count", group_by=["region"], epsilon=0.5)
        assert q.group_by == ["region"]

    def test_grouped_count_requires_group_by(self):
        with pytest.raises(ValidationError, match="group_by"):
            QuerySpec(query_type="grouped_count", epsilon=1.0)

    def test_mean_requires_value_column(self):
        with pytest.raises(ValidationError, match="value_column"):
            QuerySpec(query_type="mean", epsilon=1.0)

    def test_gaussian_requires_positive_delta(self):
        with pytest.raises(ValidationError, match="delta"):
            QuerySpec(query_type="count", epsilon=1.0, mechanism="gaussian", delta=0)

    def test_epsilon_must_be_positive(self):
        with pytest.raises(ValidationError):
            QuerySpec(query_type="count", epsilon=0)

    def test_epsilon_max_limit(self):
        with pytest.raises(ValidationError):
            QuerySpec(query_type="count", epsilon=11.0)

    def test_valid_histogram(self):
        q = QuerySpec(query_type="histogram", value_column="age", epsilon=0.5, bins=10)
        assert q.bins == 10

    def test_valid_top_k(self):
        q = QuerySpec(query_type="top_k", group_by=["region"], k=3, epsilon=0.5)
        assert q.k == 3

    def test_valid_gaussian(self):
        q = QuerySpec(query_type="count", epsilon=1.0, mechanism="gaussian", delta=1e-5)
        assert q.mechanism == "gaussian"


class TestRowRestrictions:
    def test_default_empty(self):
        q = QuerySpec(query_type="count", epsilon=1.0)
        assert q.row_restrictions == []

    def test_accepts_same_shape_as_filters(self):
        q = QuerySpec(query_type="count", epsilon=1.0, row_restrictions=[
            {"column": "active", "operator": "equals", "value": "true"}
        ])
        assert q.row_restrictions[0].column == "active"
        assert q.row_restrictions[0].operator == "equals"

    def test_max_length_ten(self):
        restrictions = [
            {"column": f"c{i}", "operator": "equals", "value": "v"} for i in range(11)
        ]
        with pytest.raises(ValidationError):
            QuerySpec(query_type="count", epsilon=1.0, row_restrictions=restrictions)

    def test_ten_restrictions_accepted(self):
        restrictions = [
            {"column": f"c{i}", "operator": "equals", "value": "v"} for i in range(10)
        ]
        q = QuerySpec(query_type="count", epsilon=1.0, row_restrictions=restrictions)
        assert len(q.row_restrictions) == 10

    def test_empty_restrictions_and_filters_is_default_behaviour(self):
        """Confirms the two new fields do not change the default query shape."""
        q = QuerySpec(query_type="count", epsilon=1.0)
        assert q.row_restrictions == []
        assert q.filters == []


class TestFilterSpec:
    def test_valid_filter(self):
        f = FilterSpec(column="region", operator="equals", value="north")
        assert f.operator == "equals"

    def test_invalid_operator(self):
        with pytest.raises(ValidationError):
            FilterSpec(column="region", operator="like", value="north")

    def test_empty_column_rejected(self):
        with pytest.raises(ValidationError):
            FilterSpec(column="", operator="equals", value="north")


class TestColumnSchemaSensitive:
    def test_is_sensitive_defaults_false(self):
        c = ColumnSchema(name="diagnosis", data_type="text")
        assert c.is_sensitive is False

    def test_is_sensitive_can_be_set(self):
        c = ColumnSchema(name="diagnosis", data_type="text", is_sensitive=True)
        assert c.is_sensitive is True


class TestDatasetSchema:
    def test_column_names(self):
        schema = DatasetSchema(columns=[
            ColumnSchema(name="age", data_type="int", can_measure=True, lower_bound=0, upper_bound=100),
            ColumnSchema(name="region", data_type="text", can_group=True),
        ])
        assert schema.column_names == {"age", "region"}

    def test_groupable_columns(self):
        schema = DatasetSchema(columns=[
            ColumnSchema(name="age", data_type="int"),
            ColumnSchema(name="region", data_type="text", can_group=True),
        ])
        assert schema.groupable_columns == {"region"}

    def test_get_column(self):
        schema = DatasetSchema(columns=[
            ColumnSchema(name="age", data_type="int"),
        ])
        assert schema.get_column("age") is not None
        assert schema.get_column("missing") is None


class TestPolicySpec:
    def test_valid_policy(self):
        p = PolicySpec(epsilon_total=5.0, epsilon_used=1.0, delta_total=1e-6)
        assert p.epsilon_total == 5.0

    def test_remaining_budget(self):
        p = PolicySpec(epsilon_total=5.0, epsilon_used=2.0, delta_total=1e-6)
        assert p.epsilon_total - p.epsilon_used == 3.0

    def test_entity_column_and_max_contributions_default(self):
        p = PolicySpec(epsilon_total=5.0, epsilon_used=1.0, delta_total=1e-6)
        assert p.entity_column is None
        assert p.max_contributions == 1

    def test_entity_column_and_max_contributions_can_be_set(self):
        p = PolicySpec(
            epsilon_total=5.0, epsilon_used=1.0, delta_total=1e-6,
            entity_column="person_id", max_contributions=7,
        )
        assert p.entity_column == "person_id"
        assert p.max_contributions == 7

    def test_max_contributions_rejects_zero(self):
        with pytest.raises(ValidationError):
            PolicySpec(epsilon_total=5.0, epsilon_used=1.0, delta_total=1e-6, max_contributions=0)

    def test_max_contributions_rejects_over_100(self):
        with pytest.raises(ValidationError):
            PolicySpec(epsilon_total=5.0, epsilon_used=1.0, delta_total=1e-6, max_contributions=101)
