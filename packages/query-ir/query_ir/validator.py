"""Query validation against dataset schema and privacy policy.

Validates that a ``QuerySpec`` references valid columns, respects bounds,
and does not exceed the remaining privacy budget.
"""

from __future__ import annotations

from query_ir.models import DatasetSchema, PolicySpec, QuerySpec


class ValidationError(Exception):
    """Raised when a query fails validation."""


def validate_query(
    spec: QuerySpec,
    schema: DatasetSchema,
    policy: PolicySpec,
) -> None:
    """Validate a query spec against the dataset schema and policy.

    Raises ``ValidationError`` with a descriptive message on failure.
    """
    # 1. Query type allowed?
    if spec.query_type not in policy.allowed_query_types:
        raise ValidationError(
            f"Query type '{spec.query_type}' is not allowed by the privacy policy"
        )

    # 2. All referenced columns exist?
    referenced = set()
    for f in spec.filters:
        referenced.add(f.column)
    for g in spec.group_by:
        referenced.add(g)
    if spec.value_column:
        referenced.add(spec.value_column)

    unknown = referenced - schema.column_names
    if unknown:
        raise ValidationError(
            f"Unknown columns: {', '.join(sorted(unknown))}"
        )

    # 2b. Contribution bounding: the declared entity column must be a real
    # column, or the bound cannot be enforced against the file at all.
    if policy.entity_column is not None and policy.entity_column not in schema.column_names:
        raise ValidationError(
            f"Unknown entity column: {policy.entity_column}"
        )

    # 3. Group-by columns are groupable?
    for g in spec.group_by:
        col = schema.get_column(g)
        if col and not col.can_group:
            raise ValidationError(f"Column '{g}' cannot be used for grouping")

    # 3b. Sensitive columns may be measured but never grouped or filtered on:
    # a group label or filter predicate reveals the attribute directly, no
    # matter how much noise is added to the released aggregate. Column
    # *names* are already public schema metadata (unlike a row restriction's
    # column/value, which must never appear in an error -- see
    # executor.py's module docstring), so naming the column here is safe.
    for g in spec.group_by:
        col = schema.get_column(g)
        if col and col.is_sensitive:
            raise ValidationError(
                f"Column '{g}' is sensitive and cannot be used for grouping"
            )
    for f in spec.filters:
        col = schema.get_column(f.column)
        if col and col.is_sensitive:
            raise ValidationError(
                f"Column '{f.column}' is sensitive and cannot be used as a filter"
            )

    # 4. Value column is measurable and has bounds?
    if spec.value_column:
        col = schema.get_column(spec.value_column)
        if not col:
            raise ValidationError(f"Value column '{spec.value_column}' not found")
        if not col.can_measure:
            raise ValidationError(
                f"Column '{spec.value_column}' cannot be used for measurement"
            )
        if col.lower_bound is None or col.upper_bound is None:
            raise ValidationError(
                f"Column '{spec.value_column}' needs public clipping bounds"
            )
        if col.lower_bound >= col.upper_bound:
            raise ValidationError(
                f"Invalid bounds for '{spec.value_column}': "
                f"[{col.lower_bound}, {col.upper_bound}]"
            )

    # 5. Grouped queries need public categories?
    if spec.query_type in ("grouped_count", "top_k") and spec.group_by:
        for g in spec.group_by:
            cats = policy.public_categories.get(g, [])
            if not cats:
                raise ValidationError(
                    f"Grouped column '{g}' needs a public category domain"
                )

    # 6. Filter operators are valid (already enforced by Pydantic but double-check)?
    for f in spec.filters:
        if f.operator not in ("equals", "not_equals"):
            raise ValidationError(f"Invalid filter operator: {f.operator}")

    # 7. Budget check
    remaining = policy.epsilon_total - policy.epsilon_used
    if spec.epsilon > remaining:
        raise ValidationError(
            f"Insufficient privacy budget: requested {spec.epsilon}, "
            f"remaining {remaining:.4f}"
        )

    # 8. Gaussian requires delta budget
    if spec.mechanism == "gaussian" and spec.delta > 0:
        if spec.delta > policy.delta_total:
            raise ValidationError(
                f"Requested delta {spec.delta} exceeds policy delta {policy.delta_total}"
            )


def estimate_sensitivity(
    spec: QuerySpec,
    schema: DatasetSchema,
) -> float:
    """Estimate the global sensitivity for a query.

    Returns the sensitivity used to calibrate noise.
    """
    if spec.query_type in ("count", "grouped_count", "top_k"):
        return 1.0

    if spec.query_type == "histogram":
        return 1.0

    if spec.query_type in ("bounded_sum", "mean") and spec.value_column:
        col = schema.get_column(spec.value_column)
        if col and col.lower_bound is not None and col.upper_bound is not None:
            if spec.query_type == "bounded_sum":
                return max(abs(col.lower_bound), abs(col.upper_bound))
            else:
                # Mean sensitivity is (upper - lower) for the sum component
                return col.upper_bound - col.lower_bound
        raise ValueError(f"Cannot compute sensitivity: missing bounds for {spec.value_column}")

    return 1.0


def estimate_privacy_cost(spec: QuerySpec) -> tuple[float, float]:
    """Return the (epsilon, delta) cost of a query.

    For Laplace queries, delta is 0.  For Gaussian queries, delta is as
    specified in the query spec.
    """
    delta = spec.delta if spec.mechanism == "gaussian" else 0.0
    return (spec.epsilon, delta)
