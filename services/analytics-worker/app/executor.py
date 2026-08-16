"""Execute differentially private aggregate queries against Parquet files.

Supports both Laplace and Gaussian mechanisms.  Gaussian covers count,
grouped_count, top_k, histogram, and bounded_sum; it is refused for mean,
because the bounded mean's existing epsilon split between a noisy sum and a
noisy count would need its own justification for how to split delta too, and
guessing at that split would be worse than refusing (see the mechanism/delta
checks near the top of ``execute``).  Gaussian sigma is always calibrated
with ``dp_core.mechanisms.gaussian.calibrate_sigma_analytic`` (Balle-Wang
2018), never the classical ``calibrate_sigma``, because the classical bound
rejects epsilon > 1 and this platform's queries are not bounded to epsilon
<= 1.  Column references use quoted identifiers to prevent injection.  Group
selection uses noisy thresholding against a public category domain.

Invariant -- the error channel must stay data-independent.  Privacy budget is
reserved before this module runs, so by the time execution starts the analyst
is guaranteed a response.  Every ``raise`` below must therefore depend only on
public metadata: the requested query type, the policy, and the owner-supplied
bounds and category domain.  Nothing may raise, return a different set of
keys, or change a value's type based on what is actually in the file -- a
raise conditioned on row content hands the analyst an exact, un-noised bit
that no amount of added noise can take back.  Validation that depends only on
public metadata belongs upstream in the API route, before reservation.

Adjacency and contribution bounding -- every sensitivity in this module
assumes add/remove-one-ENTITY adjacency, where one entity may contribute at
most ``max_contributions`` rows.  Add/remove-one-ROW adjacency, the
historical assumption, is the special case ``entity_column=None``
(equivalently ``max_contributions=1``): no entity column means there is no
column to bound contributions by, so the row itself is treated as the
privacy unit, exactly as before.  When ``entity_column`` is set, every row
belonging to an entity beyond the ``max_contributions``'th (in a fixed,
deterministic order -- see ``query_ir.compiler.compile_entity_bounded_source``)
is dropped before any aggregate runs, and every noise scale below is
multiplied by ``max_contributions`` to match.  Introspecting the file's
column list to build that deterministic order is schema access, not a
row-content read, so it does not weaken the data-independence invariant
above -- the column list is the same for every row in the file, matched or
not.

Row restrictions -- owner-defined predicates, AND-ed into the query ahead of
the analyst's own filters.  They do not change sensitivity: excluding rows
for the same, data-independent reason on every query moves any aggregate by
an amount that does not depend on what the excluded rows contained, so no
noise-calibration change is needed for them (contrast with contribution
bounding above, which does change every noise scale).  Order chosen: entity
contribution bounding runs first (in ``compile_entity_bounded_source``'s
inner subquery, over the whole file), then restrictions and analyst filters
apply together in the outer query's WHERE clause, restrictions first.
Restrictions only remove rows, never add them, so an entity capped at
``max_contributions`` rows still contributes at most that many after
restrictions are applied -- the sensitivity bound established by bounding
is unaffected by doing it first.  Applying restrictions after bounding also
keeps the deterministic per-entity row selection unaffected by which rows a
restriction happens to remove, so releases stay reproducible.

Row restrictions are owner configuration and may themselves encode a
sensitive predicate (e.g. excluding one specific entity).  Because of that,
a row restriction's column or value must never appear in an error message
or any response field -- see ``_reject_unknown_restriction_columns`` below,
the one place a restriction can fail.  A restriction that matches zero rows
is not an error at all: it must release exactly like any other query (see
the data-independence invariant above), the same as an analyst filter that
matches nothing.
"""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import structlog
from pydantic import ValidationError as PydanticValidationError

from . import privacy
from .privacy import (
    private_bounded_mean_release,
    private_bounded_sum,
    private_count_discrete,
)
from dp_core.mechanisms.laplace import confidence_radius
# Imported as a module, not individual names, so that a test monkeypatching
# ``dp_gaussian.gaussian_noise`` silences noise everywhere it is used below
# -- both inside the *_analytic release functions (which reference the name
# through this same module object) and in the grouped-selection code in this
# file, which calls ``dp_gaussian.gaussian_noise`` directly rather than a
# locally-bound import. A "from ... import gaussian_noise" here would create
# a second, independent binding that the same patch would silently miss.
from dp_core.mechanisms import gaussian as dp_gaussian
from dp_core.sensitivity.calculator import bounded_sum_sensitivity
from query_ir.compiler import compile_entity_bounded_source, compile_to_duckdb, quote_identifier
from query_ir.models import FilterSpec, QuerySpec

#: Every SQL string this module runs is produced by ``query_ir.compiler``.
#: Identifier quoting and filter parameterisation live there so that a
#: hardening fix cannot land in one compiler and miss another.
TABLE_EXPR = "read_parquet(?)"

# See the privacy-constraint comment in app/config.py. This module computes
# true (un-noised) aggregates as local variables (`raw`, `observed`, `counts`,
# `rows`, `selected`, ...) before noise is added -- none of those may ever be
# passed to this logger. Only query shape (type, mechanism) is safe here.
logger = structlog.get_logger(__name__)


# Sensitivity scaling lives in dp-core's mechanisms, which take
# ``max_contributions`` directly, rather than being reimplemented here. An
# earlier revision carried scaled copies of the three mechanisms in this file;
# that meant a fix to a mechanism could land in dp-core and silently miss the
# entity-bounded path. One implementation, one place to fix.

def _spec(
    query_type: str,
    epsilon: float,
    filters: list[dict[str, str]] | None,
    group_by: str | None,
    value_column: str | None,
    bins: int,
    top_k: int,
    row_restrictions: list[dict[str, str]] | None = None,
) -> QuerySpec:
    """Build the typed spec the compiler consumes.

    Pydantic's ValidationError is a ValueError subclass, so it would already
    surface as a 400.  It is re-raised as a plain ValueError so the analyst
    sees the constraint that failed rather than the internal model layout --
    except this message is intentionally generic (not the ValidationError's
    own text) because that text could otherwise quote a malformed row
    restriction's column or value back to the caller. See the module
    docstring's "Row restrictions" section.
    """
    try:
        return QuerySpec(
            query_type=query_type,  # type: ignore[arg-type]
            filters=[FilterSpec(**item) for item in (filters or [])],
            row_restrictions=[FilterSpec(**item) for item in (row_restrictions or [])],
            group_by=[group_by] if group_by else [],
            value_column=value_column,
            bins=bins,
            k=top_k,
            epsilon=epsilon,
        )
    except PydanticValidationError as error:
        raise ValueError("Unsupported or incomplete query") from error


def _reject_unknown_restriction_columns(
    con: duckdb.DuckDBPyConnection, path: Path, row_restrictions: list[FilterSpec],
) -> None:
    """Raise a static, content-free error if a restriction references a
    column absent from the file.

    A restriction column missing from the file is an owner-side
    configuration error, not something the analyst caused -- and the
    restriction may itself encode a sensitive predicate (see the module
    docstring), so the error deliberately does not name the column or the
    value, unlike an analyst-supplied filter referencing an unknown column.

    Reading the column list via ``LIMIT 0`` is schema access, not a
    row-content read -- the column list is identical for every row in the
    file, matched or not -- so this check does not weaken the
    data-independence invariant at the top of this module.
    """
    if not row_restrictions:
        return
    description = con.execute(
        f"SELECT * FROM {TABLE_EXPR} LIMIT 0", [str(path)]
    ).description
    columns = {column[0] for column in (description or [])}
    for restriction in row_restrictions:
        if restriction.column not in columns:
            raise ValueError(
                "Row restriction configuration is invalid. Contact the "
                "dataset owner."
            )


def _entity_bounded_table(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    entity_column: str,
    max_contributions: int,
) -> tuple[str, list[object]]:
    """Build a table expression capping each entity at ``max_contributions`` rows.

    ``compile_entity_bounded_source`` needs an explicit column list to order
    by, because DuckDB does not guarantee scan order and rejects ``ORDER BY
    ALL`` inside a window clause.  The list is read from the file's schema
    with ``LIMIT 0``, which returns column metadata without reading a single
    row -- schema access, not row content, so the data-independence invariant
    at the top of this module still holds: the column list is identical
    whether or not any row matches the query.
    """
    description = con.execute(
        f"SELECT * FROM {TABLE_EXPR} LIMIT 0", [str(path)]
    ).description
    order_columns = [column[0] for column in (description or [])]
    return compile_entity_bounded_source(
        TABLE_EXPR, entity_column, max_contributions, order_columns,
    )


def execute(
    path: Path,
    query_type: str,
    epsilon: float,
    group_by: str | None = None,
    value_column: str | None = None,
    lower: float | None = None,
    upper: float | None = None,
    bins: int = 6,
    filters: list[dict[str, str]] | None = None,
    min_group_size: int = 5,
    max_groups: int = 20,
    top_k: int = 5,
    minimum_denominator: int = 10,
    group_categories: list[str] | None = None,
    mechanism: str = "laplace",
    delta: float = 0.0,
    entity_column: str | None = None,
    max_contributions: int = 1,
    row_restrictions: list[dict[str, str]] | None = None,
):
    """Execute a private aggregate query on a local Parquet file.

    Args:
        path: Local path to the decrypted Parquet file.
        query_type: One of count, grouped_count, mean, bounded_sum, histogram, top_k.
        epsilon: Privacy parameter.
        mechanism: "laplace" (default) or "gaussian". Laplace requires
            delta=0; Gaussian requires delta > 0 -- each is rejected with
            the other's delta, rather than silently ignoring it. Gaussian
            is refused for query_type="mean" (see the module docstring).
        delta: Required (> 0) when mechanism is "gaussian"; must be 0 for
            "laplace". Gaussian sigma is calibrated with
            ``calibrate_sigma_analytic``, valid for every epsilon > 0.
        entity_column: Column identifying a person/entity.  When set, each
            entity's contribution is capped at ``max_contributions`` rows
            before aggregation, and every sensitivity is scaled by
            ``max_contributions`` to match -- add/remove-one-ENTITY
            adjacency instead of add/remove-one-ROW.  ``None`` (the default)
            preserves the historical row-adjacency assumption exactly,
            regardless of ``max_contributions``: there is no column to bound
            contributions by, so the row is the privacy unit.
        max_contributions: Maximum rows one entity may contribute. Only
            enforced when ``entity_column`` is set.
        row_restrictions: Owner-defined equals/not_equals predicates AND-ed
            in ahead of ``filters``. Empty (the default) reproduces today's
            behaviour exactly. See the module docstring's "Row restrictions"
            section for ordering and the leak-prevention rule that governs
            how a malformed restriction may fail.
        (remaining args): Query-specific parameters.
    """
    if epsilon <= 0 or max_groups < 1 or max_contributions < 1:
        raise ValueError("Positive epsilon, max_groups, and max_contributions are required")

    # Mechanism/delta consistency. Both checks depend only on public
    # metadata -- the requested mechanism, delta, and query type -- never on
    # file contents, so they satisfy the data-independence invariant at the
    # top of this module.
    if mechanism == "gaussian" and delta <= 0:
        raise ValueError(
            "mechanism=gaussian requires delta > 0. The Gaussian mechanism "
            "is (epsilon, delta)-differentially private and delta is spent, "
            "not free; a request for it with delta=0 cannot be honoured."
        )
    if mechanism == "laplace" and delta > 0:
        raise ValueError(
            "mechanism=laplace does not accept delta > 0. Laplace is a pure "
            "epsilon mechanism with no delta term to spend, so a caller "
            "asking for one has a bug and should be told rather than have "
            "the delta silently ignored. Use mechanism=gaussian, or delta=0."
        )
    if mechanism == "gaussian" and query_type == "mean":
        # The bounded mean already splits epsilon between a noisy sum and a
        # noisy count (see the mean/bounded_sum branch below). Splitting
        # delta across those same two noisy releases as well is a design
        # decision -- how much of the delta budget goes to which half, and
        # whether an even split is even the right shape -- that needs its
        # own justification. Guessing at it would be worse than refusing:
        # an unexamined delta split is exactly the kind of silent
        # mis-calibration this platform's Gaussian rollout has to avoid.
        raise ValueError(
            "Gaussian is not supported for mean. The bounded mean splits "
            "epsilon between a noisy sum and a noisy count; splitting delta "
            "across both as well is a design decision that needs its own "
            "justification, and guessing at it would be worse than "
            "refusing. Use mechanism=laplace for mean, or choose a "
            "supported Gaussian query type: count, grouped_count, top_k, "
            "histogram, bounded_sum."
        )

    logger.debug(
        "executor_start",
        query_type=query_type,
        mechanism=mechanism,
        entity_bounded=entity_column is not None,
    )
    spec = _spec(
        query_type, epsilon, filters, group_by, value_column, bins, top_k,
        row_restrictions,
    )

    con = duckdb.connect()
    try:
        # Bound each entity's contribution before anything is aggregated.
        # entity_column=None keeps table_expr/table_params identical to
        # before this feature existed, and k=1, so that path's SQL and
        # noise calls are byte-for-byte what they always were.
        if entity_column:
            k = max_contributions
            bounded_expr, bound_params = _entity_bounded_table(con, path, entity_column, k)
            table_expr = bounded_expr
            table_params = [str(path), *bound_params]
        else:
            k = 1
            table_expr = TABLE_EXPR
            table_params = [str(path)]

        # Static, content-free failure only -- see the function's docstring
        # and the module docstring's "Row restrictions" section. A no-op
        # when row_restrictions is empty, so this adds no file access on the
        # (default, common) path that has none.
        _reject_unknown_restriction_columns(con, path, spec.row_restrictions)

        meta = {
            "entity_column": entity_column,
            "max_contributions": k,
            "privacy_unit": "entity" if entity_column else "row",
        }

        # -- COUNT --
        if query_type == "count":
            sql, parameters = compile_to_duckdb(spec, table_expr)
            raw = con.execute(sql, [*table_params, *parameters]).fetchone()[0]
            if mechanism == "gaussian":
                value, sigma = dp_gaussian.private_count_gaussian_analytic(raw, epsilon, delta, k)
                return {
                    "value": value,
                    "range": round(dp_gaussian.gaussian_confidence_radius(sigma), 2),
                    "mechanism": "gaussian",
                    "delta": delta,
                    "sigma": sigma,
                    **meta,
                }
            value = private_count_discrete(raw, epsilon, k)
            range_val = round(confidence_radius(epsilon, k), 2)
            return {
                "value": value,
                "range": range_val,
                "mechanism": "laplace",
                **meta,
            }

        # -- GROUPED COUNT / TOP-K --
        if query_type in ("grouped_count", "top_k") and group_by:
            if not group_categories:
                raise ValueError("A public category domain is required for grouped releases")
            sql, parameters = compile_to_duckdb(spec, table_expr)
            observed = dict(con.execute(sql, [*table_params, *parameters]).fetchall())
            # Iterate the public domain, not the observed keys: a category
            # that exists only in the data must never reach the output, and a
            # public category with no rows must still be considered so its
            # absence is decided by noise rather than by the data.
            rows = [(group, observed.get(str(group), 0)) for group in group_categories]

            # Half the budget selects which groups clear the threshold, half
            # releases their counts.  Groups partition the rows, so each half
            # composes in parallel across groups and the total cost is epsilon.
            selection_epsilon = epsilon / 2
            release_epsilon = epsilon / 2

            if mechanism == "gaussian":
                # Delta is split the same way epsilon already is, immediately
                # above: selection and release are two sequential
                # (epsilon/2, delta/2)-DP releases, which compose by basic
                # sequential composition to (epsilon, delta) overall. This
                # mirrors the existing 50/50 epsilon split exactly rather
                # than introducing a new, unjustified split ratio -- unlike
                # the bounded mean's sum/count split (refused above for
                # Gaussian), both halves here release the same kind of
                # quantity (a count) at the same sensitivity k, so an even
                # split needs no further justification.
                selection_delta = delta / 2
                release_delta = delta / 2
                selection_sigma = dp_gaussian.calibrate_sigma_analytic(
                    float(k), selection_epsilon, selection_delta
                )
                release_sigma = dp_gaussian.calibrate_sigma_analytic(
                    float(k), release_epsilon, release_delta
                )
                suppression_margin = dp_gaussian.gaussian_confidence_radius(selection_sigma)
            else:
                # Comparing a noisy count against min_group_size alone barely
                # suppresses: at selection_epsilon = 0.2 the Laplace scale is 5,
                # so an empty group clears a threshold of 5 about 18% of the
                # time. Requiring the noisy count to clear the policy minimum
                # by the 95% confidence radius of the selection noise drops
                # that below 2.5%. Scaled by k: an entity contributing up to k
                # rows can move a single group's noisy selection count by up
                # to k, same as the release noise below.
                suppression_margin = confidence_radius(selection_epsilon, k)
            threshold = min_group_size + suppression_margin

            selected = []
            for group, count in rows:
                if mechanism == "gaussian":
                    noisy_selection = count + dp_gaussian.gaussian_noise(selection_sigma)
                else:
                    noisy_selection = count + privacy.discrete_laplace(k / selection_epsilon)
                if noisy_selection >= threshold:
                    if mechanism == "gaussian":
                        released, _ = dp_gaussian.private_count_gaussian_analytic(
                            count, release_epsilon, release_delta, k
                        )
                        item_range = round(dp_gaussian.gaussian_confidence_radius(release_sigma), 2)
                    else:
                        released = private_count_discrete(count, release_epsilon, k)
                        item_range = round(confidence_radius(release_epsilon, k), 2)
                    selected.append({
                        "group": str(group),
                        "value": released,
                        "range": item_range,
                    })

            selected.sort(key=lambda item: item["value"], reverse=True)
            limit = min(max_groups, top_k if query_type == "top_k" else max_groups)

            if mechanism == "gaussian":
                result = {
                    "values": selected[:limit],
                    "selection": "public_domain_noisy_threshold",
                    "truncated": len(selected) > limit,
                    "range": round(dp_gaussian.gaussian_confidence_radius(release_sigma), 2),
                    "suppression_threshold": round(threshold, 2),
                    "min_group_size": min_group_size,
                    "mechanism": "gaussian",
                    "delta": delta,
                    "sigma": release_sigma,
                    **meta,
                }
                return result

            range_val = round(confidence_radius(release_epsilon, k), 2)
            return {
                "values": selected[:limit],
                "selection": "public_domain_noisy_threshold",
                "truncated": len(selected) > limit,
                "range": range_val,
                "suppression_threshold": round(threshold, 2),
                "min_group_size": min_group_size,
                "mechanism": "laplace",
                **meta,
            }

        # -- MEAN / BOUNDED SUM --
        if query_type in ("mean", "bounded_sum") and value_column:
            if lower is None or upper is None or lower >= upper:
                raise ValueError("Valid public bounds are required")
            # The compiler emits coalesce(...) so an empty result set returns
            # (0, 0) rather than SQL NULL.  Whether any row matched is
            # private: it must not change the response shape, the status
            # code, or whether this function raises.
            sql, parameters = compile_to_duckdb(spec, table_expr)
            raw, count = con.execute(sql, [upper, lower, *table_params, *parameters]).fetchone()

            if query_type == "bounded_sum":
                if mechanism == "gaussian":
                    value, sigma = dp_gaussian.private_sum_gaussian_analytic(
                        float(raw), epsilon, delta, lower, upper, k
                    )
                    return {
                        "value": round(value, 2),
                        "bounds": [lower, upper],
                        "range": round(dp_gaussian.gaussian_confidence_radius(sigma), 2),
                        "mechanism": "gaussian",
                        "delta": delta,
                        "sigma": sigma,
                        **meta,
                    }
                value = private_bounded_sum(float(raw), epsilon, lower, upper, k)
                sensitivity = bounded_sum_sensitivity(lower, upper, k)
                return {
                    "value": round(value, 2),
                    "bounds": [lower, upper],
                    "range": round(confidence_radius(epsilon, sensitivity), 2),
                    "mechanism": "laplace",
                    **meta,
                }

            # mean: mechanism is always "laplace" here -- Gaussian is refused
            # for mean by the check near the top of execute().
            release = private_bounded_mean_release(
                float(raw), count, epsilon, lower, upper, minimum_denominator, k,
            )
            return {
                "value": release.value,
                "bounds": [lower, upper],
                "minimum_denominator": minimum_denominator,
                # Sum-noise term only; see private_bounded_mean_release. The
                # denominator is the noisy count floored at the public
                # minimum, so on a small population the radius is large and
                # the released mean is close to meaningless -- which is
                # exactly what the analyst needs to be told.
                "range": release.confidence_radius,
                "range_basis": "sum_noise_only",
                "mechanism": "laplace",
                **meta,
            }

        # -- HISTOGRAM --
        if query_type == "histogram" and value_column:
            if lower is None or upper is None or lower >= upper:
                raise ValueError("Valid public bounds are required")
            sql, parameters = compile_to_duckdb(spec, table_expr)
            rows = con.execute(sql, [*table_params, *parameters]).fetchall()
            step = (upper - lower) / bins
            counts = [0] * bins
            for (value,) in rows:
                numeric = float(value)
                # NaN and infinity survive the SQL "is not null" filter and
                # would make int() raise, turning the presence of one bad
                # value into a data-dependent error.  Drop them instead.
                if not math.isfinite(numeric):
                    continue
                index = min(bins - 1, max(0, int((numeric - lower) / step)))
                counts[index] += 1

            if mechanism == "gaussian":
                # Buckets partition the rows exactly like grouped-count
                # categories, so the same sigma calibrated once up front
                # (public metadata only -- epsilon, delta, k) applies to
                # every bucket; no per-bucket delta split is needed because
                # there is no selection phase here, unlike grouped_count.
                sigma = dp_gaussian.calibrate_sigma_analytic(float(k), epsilon, delta)
                gaussian_range = round(dp_gaussian.gaussian_confidence_radius(sigma), 2)
                return {
                    "buckets": [
                        {
                            "from": lower + i * step,
                            "to": upper if i == bins - 1 else lower + (i + 1) * step,
                            "value": dp_gaussian.private_count_gaussian_analytic(
                                c, epsilon, delta, k
                            )[0],
                            "range": gaussian_range,
                        }
                        for i, c in enumerate(counts)
                    ],
                    "mechanism": "gaussian",
                    "delta": delta,
                    "sigma": sigma,
                    **meta,
                }

            range_val = round(confidence_radius(epsilon, k), 2)
            return {
                "buckets": [
                    {
                        "from": lower + i * step,
                        "to": upper if i == bins - 1 else lower + (i + 1) * step,
                        "value": private_count_discrete(c, epsilon, k),
                        "range": range_val,
                    }
                    for i, c in enumerate(counts)
                ],
                "mechanism": "laplace",
                **meta,
            }

        raise ValueError("Unsupported or incomplete query")
    finally:
        con.close()
