"""Veil query-ir: typed query representation, validation, and compilation."""

from query_ir.models import ColumnSchema, DatasetSchema, FilterSpec, PolicySpec, QuerySpec
from query_ir.validator import estimate_privacy_cost, estimate_sensitivity, validate_query
from query_ir.compiler import compile_filter_clause, compile_to_duckdb

__all__ = [
    "QuerySpec",
    "FilterSpec",
    "ColumnSchema",
    "DatasetSchema",
    "PolicySpec",
    "validate_query",
    "estimate_sensitivity",
    "estimate_privacy_cost",
    "compile_to_duckdb",
    "compile_filter_clause",
]
