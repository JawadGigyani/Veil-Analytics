"""Compile a QuerySpec into a safe, parameterized DuckDB SQL expression.

No raw user input is interpolated into SQL strings.  Column names use
quoted identifiers and filter values use query parameters.
"""

from __future__ import annotations

from query_ir.models import FilterSpec, QuerySpec


def quote_identifier(name: str) -> str:
    """Quote a SQL identifier to prevent injection."""
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def compile_filter_clause(
    filters: list[FilterSpec],
) -> tuple[str, list[str]]:
    """Compile filters into a WHERE clause and parameter list.

    Returns:
        A tuple of (clause_string, parameters).
        If no filters, clause_string is empty.
    """
    if not filters:
        return ("", [])

    clauses = []
    params = []
    for f in filters:
        op = "=" if f.operator == "equals" else "<>"
        clauses.append(f"cast({quote_identifier(f.column)} as varchar) {op} ?")
        params.append(f.value)

    return (f" WHERE {' AND '.join(clauses)}", params)


def compile_entity_bounded_source(
    table_expr: str,
    entity_column: str,
    max_contributions: int,
    order_columns: list[str],
) -> tuple[str, list[object]]:
    """Wrap a table expression to keep at most ``max_contributions`` rows
    per entity, enforcing add/remove-one-ENTITY adjacency.

    Without this, sensitivity calibrated for add/remove-one-ROW adjacency is
    wrong by a factor of however many rows a single entity contributes: an
    entity with many rows can move an aggregate far more than the noise
    added to protect it accounts for.  Capping each entity's contribution
    before any aggregate runs is what makes ``max_contributions`` the true
    bound sensitivity can be calibrated against.

    Uses ``row_number() OVER (PARTITION BY <entity> ORDER BY ...)`` and
    keeps rows ranked ``<= max_contributions``.  The ORDER BY is built from
    an explicit column list rather than left unordered or delegated to
    ``ORDER BY ALL`` (which DuckDB does not accept inside a window
    function's OVER clause): DuckDB does not guarantee scan order, and an
    aggregate that silently varied between two runs over the same file
    would make the release non-reproducible.

    Args:
        table_expr: The table expression to bound, e.g. ``read_parquet(?)``.
            Any placeholders inside it are the caller's own and are left
            untouched.
        entity_column: The column identifying an entity. Quoted internally.
        max_contributions: The maximum rows any one entity may keep.
        order_columns: Column names used, in order, as the deterministic
            tie-breaker within each entity's partition. Quoted internally.

    Returns:
        A tuple of (wrapped_table_expr, params). ``params`` holds exactly
        one value -- the max_contributions bound -- for the single
        placeholder this function adds. Because that placeholder is
        textually the first one to appear *after* whatever placeholders
        ``table_expr`` itself already contains, the caller must position
        this parameter immediately after ``table_expr``'s own parameters,
        ahead of any further filter parameters supplied to
        ``compile_to_duckdb``.

    Raises:
        ValueError: If max_contributions < 1.
    """
    if max_contributions < 1:
        raise ValueError("max_contributions must be >= 1")

    entity = quote_identifier(entity_column)
    order_by = (
        ", ".join(quote_identifier(c) for c in order_columns)
        if order_columns
        else entity
    )
    sql = (
        "(SELECT * EXCLUDE (__veil_contribution_rank) FROM ("
        "SELECT *, row_number() OVER "
        f"(PARTITION BY {entity} ORDER BY {order_by}) AS __veil_contribution_rank "
        f"FROM {table_expr}"
        ") WHERE __veil_contribution_rank <= ?)"
    )
    return (sql, [max_contributions])


def compile_to_duckdb(
    spec: QuerySpec,
    table_expr: str,
) -> tuple[str, list[object]]:
    """Compile a QuerySpec into a DuckDB SQL string and parameters.

    Args:
        spec: The validated query specification.
        table_expr: A DuckDB table expression, typically ``read_parquet(?)``.

    Returns:
        A tuple of (sql_string, parameters).  The first parameter in the
        list is always the table path placeholder when using read_parquet.

    Raises:
        ValueError: If the query type cannot be compiled.
    """
    # Row restrictions (owner-defined) are AND-ed in ahead of the analyst's
    # own filters. Both are the identical (column, operator, value) shape,
    # so the same parameterised compile_filter_clause handles both -- there
    # is no separate string-interpolation path for restrictions to miss the
    # existing filter hardening. Listing restrictions first only affects the
    # SQL text order, not the result: AND is commutative, so this is a
    # documentation/readability choice, not a correctness one.
    where, params = compile_filter_clause(spec.row_restrictions + spec.filters)

    if spec.query_type == "count":
        sql = f"SELECT count(*) FROM {table_expr}{where}"
        return (sql, params)

    if spec.query_type in ("grouped_count", "top_k") and spec.group_by:
        # One pass over the file for every category, rather than one scan per
        # public category.  The caller reindexes the result against its public
        # category domain, so categories present in the data but absent from
        # that public list are never released.
        col = quote_identifier(spec.group_by[0])
        sql = (
            f"SELECT cast({col} as varchar), count(*) "
            f"FROM {table_expr}{where} GROUP BY 1"
        )
        return (sql, params)

    if spec.query_type in ("bounded_sum", "mean") and spec.value_column:
        col = quote_identifier(spec.value_column)
        # coalesce is load-bearing for privacy, not tidiness.  Without it an
        # empty result set returns SQL NULL, and the caller's natural NULL
        # check turns "no row matched this filter" into an error the analyst
        # can observe -- an exact, un-noised bit about the data.  Returning 0
        # keeps the empty case on the same code path as any other.
        sql = (
            f"SELECT coalesce(sum(least(?, greatest(?, {col}))), 0), count({col}) "
            f"FROM {table_expr}{where}"
        )
        return (sql, params)  # caller prepends upper, lower

    if spec.query_type == "histogram" and spec.value_column:
        col = quote_identifier(spec.value_column)
        filter_expr = f"{where} AND {col} IS NOT NULL" if where else f" WHERE {col} IS NOT NULL"
        sql = f"SELECT {col} FROM {table_expr}{filter_expr}"
        return (sql, params)

    raise ValueError(f"Cannot compile query type: {spec.query_type}")
