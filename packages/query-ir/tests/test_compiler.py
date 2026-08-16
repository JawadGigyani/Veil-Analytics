"""Tests for the DuckDB SQL compiler."""

import duckdb
import pyarrow as pa
import pyarrow.parquet as parquet
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from query_ir.compiler import (
    compile_entity_bounded_source,
    compile_filter_clause,
    compile_to_duckdb,
    quote_identifier,
)
from query_ir.models import FilterSpec, QuerySpec


class TestQuoteIdentifier:
    def test_simple_name(self):
        assert quote_identifier("age") == '"age"'

    def test_name_with_quotes(self):
        assert quote_identifier('my"col') == '"my""col"'


class TestCompileFilterClause:
    def test_empty_filters(self):
        clause, params = compile_filter_clause([])
        assert clause == ""
        assert params == []

    def test_single_equals(self):
        clause, params = compile_filter_clause([
            FilterSpec(column="region", operator="equals", value="north"),
        ])
        assert "WHERE" in clause
        assert "=" in clause
        assert params == ["north"]

    def test_multiple_filters(self):
        clause, params = compile_filter_clause([
            FilterSpec(column="region", operator="equals", value="north"),
            FilterSpec(column="status", operator="not_equals", value="closed"),
        ])
        assert "AND" in clause
        assert len(params) == 2

    def test_not_equals_operator(self):
        clause, _ = compile_filter_clause([
            FilterSpec(column="x", operator="not_equals", value="y"),
        ])
        assert "<>" in clause


class TestCompileToDuckdb:
    def test_count(self):
        spec = QuerySpec(query_type="count", epsilon=1.0)
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "count(*)" in sql.lower()

    def test_count_with_filter(self):
        spec = QuerySpec(query_type="count", epsilon=1.0, filters=[
            {"column": "region", "operator": "equals", "value": "north"}
        ])
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "WHERE" in sql
        assert params == ["north"]

    def test_grouped_count(self):
        spec = QuerySpec(query_type="grouped_count", group_by=["region"], epsilon=1.0)
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert '"region"' in sql

    def test_sum(self):
        spec = QuerySpec(query_type="bounded_sum", value_column="score", epsilon=1.0)
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "sum" in sql.lower()
        assert '"score"' in sql

    def test_mean(self):
        spec = QuerySpec(query_type="mean", value_column="score", epsilon=1.0)
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "sum" in sql.lower()

    def test_histogram(self):
        spec = QuerySpec(query_type="histogram", value_column="age", epsilon=1.0)
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert '"age"' in sql

    def test_unsupported_type_raises(self):
        # Use model_construct to bypass validation and create an invalid spec
        spec = QuerySpec.model_construct(
            query_type="bounded_sum", epsilon=1.0, value_column=None,
            filters=[], group_by=[], bins=6, k=5,
            mechanism="laplace", delta=0.0,
        )
        with pytest.raises(ValueError, match="Cannot compile"):
            compile_to_duckdb(spec, "read_parquet(?)")


class TestRowRestrictionsCompilation:
    def test_row_restrictions_alone_produce_a_where_clause(self):
        spec = QuerySpec(query_type="count", epsilon=1.0, row_restrictions=[
            {"column": "active", "operator": "equals", "value": "true"}
        ])
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "WHERE" in sql
        assert params == ["true"]

    def test_row_restrictions_precede_analyst_filters_in_sql_text(self):
        spec = QuerySpec(
            query_type="count", epsilon=1.0,
            row_restrictions=[{"column": "active", "operator": "equals", "value": "true"}],
            filters=[{"column": "region", "operator": "equals", "value": "north"}],
        )
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert sql.index('"active"') < sql.index('"region"')
        assert params == ["true", "north"]

    def test_restrictions_and_filters_are_anded_together(self):
        spec = QuerySpec(
            query_type="count", epsilon=1.0,
            row_restrictions=[{"column": "active", "operator": "equals", "value": "true"}],
            filters=[{"column": "region", "operator": "equals", "value": "north"}],
        )
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert "AND" in sql

    def test_empty_row_restrictions_reproduce_filter_only_sql(self):
        with_empty = compile_to_duckdb(
            QuerySpec(query_type="count", epsilon=1.0, row_restrictions=[], filters=[
                {"column": "region", "operator": "equals", "value": "north"}
            ]),
            "read_parquet(?)",
        )
        without_field = compile_to_duckdb(
            QuerySpec(query_type="count", epsilon=1.0, filters=[
                {"column": "region", "operator": "equals", "value": "north"}
            ]),
            "read_parquet(?)",
        )
        assert with_empty == without_field

    def test_restrictions_apply_to_grouped_count(self):
        spec = QuerySpec(
            query_type="grouped_count", group_by=["region"], epsilon=1.0,
            row_restrictions=[{"column": "active", "operator": "equals", "value": "true"}],
        )
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert '"active"' in sql
        assert params == ["true"]

    def test_restrictions_apply_to_histogram(self):
        spec = QuerySpec(
            query_type="histogram", value_column="age", epsilon=1.0,
            row_restrictions=[{"column": "active", "operator": "equals", "value": "true"}],
        )
        sql, params = compile_to_duckdb(spec, "read_parquet(?)")
        assert '"active"' in sql
        assert "IS NOT NULL" in sql
        assert params == ["true"]


def write_parquet(tmp_path, data):
    path = tmp_path / "dataset.parquet"
    parquet.write_table(pa.table(data), path)
    return path


class TestCompileEntityBoundedSource:
    def test_rejects_zero_or_negative_max_contributions(self):
        with pytest.raises(ValueError, match="max_contributions"):
            compile_entity_bounded_source("read_parquet(?)", "person", 0, ["person"])
        with pytest.raises(ValueError, match="max_contributions"):
            compile_entity_bounded_source("read_parquet(?)", "person", -1, ["person"])

    def test_quotes_entity_and_order_columns(self):
        sql, params = compile_entity_bounded_source(
            "read_parquet(?)", "person", 2, ["person", "row_id"],
        )
        assert '"person"' in sql
        assert '"row_id"' in sql
        assert params == [2]

    def test_caps_rows_per_entity(self, tmp_path):
        path = write_parquet(tmp_path, {
            "person": [1] * 50 + [2] * 3,
            "row_id": list(range(53)),
            "value": list(range(53)),
        })
        table_expr, table_params = "read_parquet(?)", [str(path)]
        sql, bound_params = compile_entity_bounded_source(
            table_expr, "person", 2, ["person", "row_id"],
        )
        con = duckdb.connect()
        rows = con.execute(
            f"SELECT person, count(*) FROM {sql} GROUP BY 1 ORDER BY 1",
            [*table_params, *bound_params],
        ).fetchall()
        con.close()
        assert rows == [(1, 2), (2, 2)]

    def test_deterministic_across_repeated_runs(self, tmp_path):
        """The kept rows must not vary run to run, or the release would not
        be reproducible over the same file."""
        path = write_parquet(tmp_path, {
            "person": [1, 1, 1, 2, 2],
            "row_id": [10, 20, 30, 40, 50],
            "value": [100, 200, 300, 400, 500],
        })
        sql, bound_params = compile_entity_bounded_source(
            "read_parquet(?)", "person", 1, ["person", "row_id"],
        )
        con = duckdb.connect()
        results = [
            con.execute(
                f"SELECT person, row_id, value FROM {sql} ORDER BY 1",
                [str(path), *bound_params],
            ).fetchall()
            for _ in range(5)
        ]
        con.close()
        assert all(result == results[0] for result in results)

    def test_falls_back_to_entity_column_when_no_order_columns_given(self, tmp_path):
        path = write_parquet(tmp_path, {"person": [1, 1, 2], "value": [1, 2, 3]})
        sql, bound_params = compile_entity_bounded_source(
            "read_parquet(?)", "person", 1, [],
        )
        con = duckdb.connect()
        rows = con.execute(
            f"SELECT count(*) FROM {sql}", [str(path), *bound_params],
        ).fetchall()
        con.close()
        assert rows == [(2,)]

    def test_composes_with_compile_to_duckdb_for_grouped_count(self, tmp_path):
        path = write_parquet(tmp_path, {
            "person": [1] * 10 + [2] * 10,
            "row_id": list(range(20)),
            "region": ["north"] * 10 + ["south"] * 10,
        })
        bounded_expr, bound_params = compile_entity_bounded_source(
            "read_parquet(?)", "person", 3, ["person", "row_id"],
        )
        spec = QuerySpec(query_type="grouped_count", group_by=["region"], epsilon=1.0)
        sql, filter_params = compile_to_duckdb(spec, bounded_expr)
        con = duckdb.connect()
        rows = con.execute(
            sql, [str(path), *bound_params, *filter_params],
        ).fetchall()
        con.close()
        assert dict(rows) == {"north": 3, "south": 3}


class TestEntityBoundedSourcePropertyBased:
    """The bound is the entire point: whatever k is, and however skewed
    entity contributions are, no entity may keep more than k rows once
    passed through the bounded source."""

    @given(
        k=st.integers(min_value=1, max_value=20),
        contributions=st.lists(st.integers(min_value=1, max_value=25), min_size=1, max_size=8),
    )
    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_bounded_source_never_exceeds_k_rows_per_entity(self, tmp_path_factory, k, contributions):
        tmp_path = tmp_path_factory.mktemp("entity_bounding")
        persons: list[int] = []
        row_ids: list[int] = []
        next_row_id = 0
        for person, n_rows in enumerate(contributions):
            persons.extend([person] * n_rows)
            row_ids.extend(range(next_row_id, next_row_id + n_rows))
            next_row_id += n_rows

        path = tmp_path / "dataset.parquet"
        parquet.write_table(pa.table({"person": persons, "row_id": row_ids}), path)

        sql, bound_params = compile_entity_bounded_source(
            "read_parquet(?)", "person", k, ["person", "row_id"],
        )
        con = duckdb.connect()
        counts = con.execute(
            f"SELECT person, count(*) FROM {sql} GROUP BY 1", [str(path), *bound_params],
        ).fetchall()
        con.close()

        for _, kept in counts:
            assert kept <= k

        # And every entity that had at least one row keeps min(contributed, k).
        expected = {
            person: min(n_rows, k) for person, n_rows in enumerate(contributions)
        }
        assert dict(counts) == expected
