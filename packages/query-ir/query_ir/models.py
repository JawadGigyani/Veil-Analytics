"""Pydantic v2 models for the query intermediate representation.

These models define the typed structure that replaces arbitrary SQL.
The frontend submits a ``QuerySpec`` which the backend validates,
estimates sensitivity for, and compiles to a safe DuckDB expression.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FilterSpec(BaseModel):
    """A single equality or inequality filter on a column."""

    column: str = Field(min_length=1)
    operator: Literal["equals", "not_equals"]
    value: str = Field(min_length=1, max_length=200)


class QuerySpec(BaseModel):
    """Typed specification for a differentially private aggregate query."""

    query_type: Literal[
        "count",
        "grouped_count",
        "bounded_sum",
        "mean",
        "histogram",
        "top_k",
    ]
    filters: list[FilterSpec] = Field(default_factory=list, max_length=5)
    # Owner-defined predicates, AND-ed in before ``filters`` (the analyst's
    # own). Same shape as FilterSpec -- equals/not_equals only -- because
    # they are compiled through the identical parameterised WHERE-clause
    # path (see query_ir.compiler.compile_to_duckdb). Restrictions do not
    # change sensitivity: removing rows for the same reason on every query
    # moves an aggregate by an amount independent of what the removed rows
    # contained, so no noise-calibration change is needed for them. See
    # executor.py's module docstring for the leak-prevention rule that
    # governs how a malformed restriction may fail.
    row_restrictions: list[FilterSpec] = Field(default_factory=list, max_length=10)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    value_column: str | None = None
    bins: int = Field(default=6, ge=2, le=50)
    k: int = Field(default=5, ge=1, le=50)
    epsilon: float = Field(gt=0, le=10.0)
    delta: float = Field(default=0.0, ge=0, lt=1.0)
    mechanism: Literal["laplace", "gaussian"] = "laplace"

    @model_validator(mode="after")
    def validate_query_requirements(self) -> "QuerySpec":
        if self.query_type in ("grouped_count", "top_k") and not self.group_by:
            raise ValueError("group_by is required for grouped_count and top_k")
        if self.query_type in ("bounded_sum", "mean", "histogram") and not self.value_column:
            raise ValueError("value_column is required for bounded_sum, mean, and histogram")
        if self.mechanism == "gaussian" and self.delta <= 0:
            raise ValueError("Gaussian mechanism requires positive delta")
        return self


class ColumnSchema(BaseModel):
    """Schema for a single dataset column."""

    name: str
    data_type: str
    can_group: bool = False
    can_measure: bool = False
    lower_bound: float | None = None
    upper_bound: float | None = None
    # A sensitive column may be measured (bounded sum, mean, histogram) but
    # may never be a group_by column or a filter column: a group label or a
    # filter predicate reveals the attribute directly, regardless of how
    # much noise is added to the released aggregate. Enforced in
    # query_ir.validator.validate_query.
    is_sensitive: bool = False


class DatasetSchema(BaseModel):
    """Schema for an entire dataset."""

    columns: list[ColumnSchema]

    def get_column(self, name: str) -> ColumnSchema | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}

    @property
    def groupable_columns(self) -> set[str]:
        return {c.name for c in self.columns if c.can_group}

    @property
    def measurable_columns(self) -> set[str]:
        return {c.name for c in self.columns if c.can_measure}


class PolicySpec(BaseModel):
    """Privacy policy constraints for a dataset."""

    epsilon_total: float = Field(gt=0)
    epsilon_used: float = Field(ge=0)
    delta_total: float = Field(ge=0, lt=1)
    min_group_size: int = Field(ge=1, default=5)
    max_groups: int = Field(ge=1, le=100, default=20)
    allowed_query_types: list[str] = Field(
        default_factory=lambda: [
            "count", "grouped_count", "bounded_sum",
            "mean", "histogram", "top_k",
        ]
    )
    public_categories: dict[str, list[str]] = Field(default_factory=dict)
    # Contribution bounding: the column identifying a person/entity, and the
    # maximum number of rows a single entity may contribute.  When
    # entity_column is set, the executor bounds each entity's contribution
    # to at most max_contributions rows before aggregating, and every
    # sensitivity calculation scales by max_contributions instead of
    # assuming add/remove-one-ROW adjacency.  entity_column=None preserves
    # today's (unenforced, one-row-per-person) adjacency assumption.
    entity_column: str | None = None
    max_contributions: int = Field(ge=1, le=100, default=1)
