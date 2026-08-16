from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

import structlog
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
import pyarrow.csv as csv
import pyarrow as pa
import pyarrow.parquet as parquet
from .config import settings
from .crypto import encrypt
from .executor import execute
from .storage import get, object_key_for_dataset, put, remove
from query_ir.models import (
    ColumnSchema,
    DatasetSchema,
    FilterSpec as IRFilterSpec,
    PolicySpec,
    QuerySpec,
)
from query_ir.validator import ValidationError, validate_query

# See the privacy-constraint comment in app/config.py -- this is the
# request-handling layer, closest to the analyst-supplied query. Log the
# query SHAPE (type, mechanism, epsilon, column names, filter COUNT), never
# a filter value, a group category value, row-restriction content, or
# anything from `execute()`'s return value beyond the mechanism name.
logger = structlog.get_logger(__name__)

app = FastAPI(title="Veil Analytics Worker", version="0.2.0")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all so an unexpected failure is never silent.

    FastAPI's own handlers for HTTPException and RequestValidationError are
    more specific and take precedence over this one, so expected 4xx
    responses are unaffected. This only fires for genuine bugs.
    """
    logger.exception("unhandled_exception", path=str(request.url.path))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _cleanup_temp(temp: str, dataset_id: str) -> None:
    """Remove a temp directory and escalate if anything survives.

    These directories hold decrypted protected data. ``ignore_errors=True``
    keeps a cleanup failure from masking the original exception in a
    ``finally`` block, but it also means failure is silent -- so the result is
    checked and a surviving directory is logged at ERROR, not INFO. Plaintext
    left on disk is a security event and must not sit in an info-level line
    nobody alerts on.
    """
    shutil.rmtree(temp, ignore_errors=True)
    if Path(temp).exists():
        logger.error(
            "temp_cleanup_failed",
            dataset_id=dataset_id,
            path_retained=True,
            reason="decrypted_data_may_remain_on_disk",
        )
        return
    logger.info("temp_cleanup_confirmed", dataset_id=dataset_id, deleted=True)


def _validate_dataset_id(dataset_id: str) -> str:
    try:
        return str(UUID(dataset_id))
    except (ValueError, AttributeError, TypeError) as error:
        raise HTTPException(400, "dataset_id must be a valid UUID") from error


class QueryFilter(BaseModel):
    column: str = Field(min_length=1)
    operator: str = Field(pattern="^(equals|not_equals)$")
    value: str


class ColumnMeta(BaseModel):
    """Owner-supplied column policy, forwarded by the API for re-validation."""

    name: str = Field(min_length=1)
    data_type: str = ""
    can_group: bool = False
    can_measure: bool = False
    lower_bound: float | None = None
    upper_bound: float | None = None
    # A sensitive column may be measured but never grouped or filtered on --
    # see query_ir.validator.validate_query, which is where this is
    # actually enforced. Mirrors payload.sensitive_columns below; a column
    # is treated as sensitive if either signal says so (see
    # _validate_against_policy).
    is_sensitive: bool = False


class QueryRequest(BaseModel):
    dataset_id: str
    # Accepted for backward compatibility but ignored; object key is derived from dataset_id.
    storage_key: str | None = None
    # Column policy and budget state, so the worker can re-check the query
    # against the dataset schema rather than trusting the caller's decision.
    # The API always sends these; they default empty so that a caller
    # exercising only the mechanism path stays valid.
    columns: list[ColumnMeta] = Field(default_factory=list, max_length=500)
    epsilon_total: float | None = None
    epsilon_used: float | None = None
    allowed_query_types: list[str] = Field(default_factory=list, max_length=20)
    query_type: str = Field(pattern="^(count|grouped_count|mean|bounded_sum|histogram|top_k)$")
    epsilon: float = Field(ge=0.1, le=2)
    group_by: str | None = None
    value_column: str | None = None
    lower: float | None = None
    upper: float | None = None
    bins: int = Field(default=6, ge=2, le=20)
    min_group_size: int = Field(default=5, ge=1)
    max_groups: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=5, ge=1, le=20)
    minimum_denominator: int = Field(default=10, ge=1)
    group_categories: list[str] = Field(default_factory=list, max_length=100)
    filters: list[QueryFilter] = Field(default_factory=list, max_length=3)
    mechanism: Literal["laplace", "gaussian"] = "laplace"
    delta: float = Field(default=0.0, ge=0, lt=1)
    # Delta budget state, mirroring epsilon_total/epsilon_used above: the API
    # sends these once the dataset's privacy policy has a real delta_total to
    # report. None (the default) means "no delta-budget figures were sent",
    # and is handled in _validate_against_policy the same way a missing
    # epsilon_total is -- as a no-op check, not as an implicit zero budget
    # that would reject every Gaussian request regardless of the actual
    # policy.
    delta_total: float | None = None
    delta_used: float | None = None
    # Contribution bounding: entity_column identifies a person/entity in the
    # dataset; max_contributions is the maximum rows a single entity may
    # contribute. entity_column=None with max_contributions=1 (the defaults)
    # reproduces today's add/remove-one-ROW behaviour exactly -- there is no
    # column to bound contributions by, so the row remains the privacy unit.
    entity_column: str | None = None
    max_contributions: int = Field(default=1, ge=1, le=100)
    # Owner-defined predicates, AND-ed into the query ahead of the
    # analyst's own `filters`. Same shape as QueryFilter (equals/not_equals
    # only). Empty (the default) reproduces today's behaviour exactly.
    # These never appear in a response or an error -- see executor.py's
    # module docstring's "Row restrictions" section.
    row_restrictions: list[QueryFilter] = Field(default_factory=list, max_length=10)
    # Column names the owner has marked sensitive: may be measured but never
    # grouped or filtered on. Union'd with each ColumnMeta.is_sensitive in
    # _validate_against_policy so either signal is honoured.
    sensitive_columns: list[str] = Field(default_factory=list)


class DeleteRequest(BaseModel):
    dataset_id: str = Field(min_length=1)
    # Accepted for backward compatibility but ignored.
    storage_key: str | None = None


def _validate_against_policy(payload: "QueryRequest") -> None:
    """Run the shared query-IR validator over a request.

    Translates the worker's flat request into the typed models that
    ``query_ir.validator`` consumes.  Raises ``ValidationError`` on rejection.
    """
    try:
        spec = QuerySpec(
            query_type=payload.query_type,  # type: ignore[arg-type]
            filters=[
                IRFilterSpec(column=f.column, operator=f.operator, value=f.value)  # type: ignore[arg-type]
                for f in payload.filters
            ],
            group_by=[payload.group_by] if payload.group_by else [],
            value_column=payload.value_column,
            bins=payload.bins,
            k=payload.top_k,
            epsilon=payload.epsilon,
            # Forwarded so query_ir.validator's delta-budget check (below)
            # actually sees what was requested, instead of silently checking
            # a Laplace-shaped spec regardless of the real request.
            mechanism=payload.mechanism,
            delta=payload.delta,
        )
    except PydanticValidationError as error:
        # QuerySpec's own model_validator rejects mechanism=gaussian with
        # delta<=0 (the same self-consistency rule execute() enforces
        # authoritatively). This function only runs when the caller sent
        # ``columns`` (see the /v1/query handler), so a request without
        # columns instead reaches that check inside execute() directly.
        # Either way, translate to query_ir's ValidationError here so the
        # caller's single except clause handles it like any other policy
        # rejection, rather than surfacing as an uncaught 500.
        raise ValidationError(str(error)) from error
    # is_sensitive is the union of the per-column flag and the top-level
    # sensitive_columns list -- the wire contract sends both, and either one
    # naming a column is enough to enforce the restriction (see
    # query_ir.validator.validate_query). Row restrictions are deliberately
    # NOT forwarded into this QuerySpec: their column/value must never
    # appear in a ValidationError message (see executor.py's module
    # docstring), and validate_query's "Unknown columns" check would name
    # one if it saw it here. The only place a restriction is checked is
    # executor.py's _reject_unknown_restriction_columns, which fails with a
    # static message instead.
    schema = DatasetSchema(
        columns=[
            ColumnSchema(
                **{
                    **column.model_dump(),
                    "is_sensitive": column.is_sensitive or column.name in payload.sensitive_columns,
                }
            )
            for column in payload.columns
        ],
    )
    # epsilon_used is the value the API read before it reserved this query, so
    # the remaining-budget check mirrors the database's. The database holds the
    # authoritative lock; this is a second gate, not the enforcement point.
    epsilon_total = payload.epsilon_total if payload.epsilon_total is not None else payload.epsilon
    epsilon_used = payload.epsilon_used if payload.epsilon_used is not None else 0.0
    # Same pattern for delta: delta_total defaults to the requested delta
    # itself (a no-op check, delta <= delta) when the caller does not send a
    # policy-derived figure, exactly like epsilon_total above. That keeps a
    # caller exercising only the mechanism path (no delta budget info
    # available) valid, while a caller that does send delta_total gets a real
    # check against it. query_ir.validator's delta check (unlike its epsilon
    # check) compares the requested delta directly against delta_total with
    # no delta_used subtraction, so delta_used is not modelled here.
    delta_total = payload.delta_total if payload.delta_total is not None else payload.delta
    policy = PolicySpec(
        epsilon_total=max(epsilon_total, 1e-9),
        epsilon_used=max(epsilon_used, 0.0),
        delta_total=max(delta_total, 0.0),
        min_group_size=payload.min_group_size,
        max_groups=payload.max_groups,
        allowed_query_types=payload.allowed_query_types or [payload.query_type],
        public_categories=(
            {payload.group_by: payload.group_categories}
            if payload.group_by and payload.group_categories
            else {}
        ),
        # Re-check the entity column and contribution bound the API decided
        # on, the same way every other policy field here is re-checked
        # rather than trusted from the caller.
        entity_column=payload.entity_column,
        max_contributions=payload.max_contributions,
    )
    validate_query(spec, schema, policy)


@app.get("/health")
def health():
    return {"status": "ok", "service": "analytics-worker"}


@app.post("/v1/ingest")
async def ingest(dataset_id: str, upload: UploadFile = File(...), x_worker_token: str = Header(default="")):
    if x_worker_token != settings.worker_token:
        raise HTTPException(401, "Invalid worker token")
    dataset_id = _validate_dataset_id(dataset_id)
    logger.info("ingest_request_received", dataset_id=dataset_id)
    data = await upload.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        logger.warning(
            "validation_rejected",
            dataset_id=dataset_id, check="upload_size", reason="exceeds_configured_limit",
        )
        raise HTTPException(413, "Upload exceeds the configured limit")
    if not upload.filename or not upload.filename.lower().endswith((".csv", ".parquet")):
        logger.warning(
            "validation_rejected",
            dataset_id=dataset_id, check="upload_format", reason="unsupported_extension",
        )
        raise HTTPException(415, "Only CSV and Parquet uploads are accepted")
    settings.storage_root.mkdir(parents=True, exist_ok=True)

    temp = tempfile.mkdtemp()
    try:
        input_path = Path(temp) / Path(upload.filename).name
        parquet_path = Path(temp) / "data.parquet"
        input_path.write_bytes(data)
        try:
            table = (
                parquet.read_table(input_path)
                if upload.filename.lower().endswith(".parquet")
                else csv.read_csv(input_path)
            )
        except (pa.ArrowException, OSError) as error:
            logger.warning(
                "validation_rejected",
                dataset_id=dataset_id, check="parse", reason="not_a_valid_tabular_dataset",
            )
            raise HTTPException(400, "The uploaded file is not a valid tabular dataset") from error
        names = table.schema.names
        if not names or table.num_rows == 0:
            logger.warning(
                "validation_rejected",
                dataset_id=dataset_id, check="shape", reason="empty_columns_or_rows",
            )
            raise HTTPException(400, "Dataset must contain at least one column and one row")
        if len(names) != len(set(names)) or any(not name.strip() for name in names):
            logger.warning(
                "validation_rejected",
                dataset_id=dataset_id, check="column_names", reason="empty_or_duplicate",
            )
            raise HTTPException(400, "Column names must be non-empty and unique")
        parquet.write_table(table, parquet_path)
        storage_key = put(
            object_key_for_dataset(dataset_id),
            encrypt(parquet_path.read_bytes(), settings.encryption_key),
        )
    finally:
        _cleanup_temp(temp, dataset_id)
    fields = [
        {
            "name": field.name,
            "data_type": str(field.type),
            "can_measure": str(field.type).startswith(("int", "double", "float", "decimal")),
            "can_group": not str(field.type).startswith(("list", "struct")),
        }
        for field in table.schema
    ]
    # rows/columns are dataset-level counts already surfaced as public
    # metadata (datasets.row_count / column_count) -- not a per-row value.
    logger.info(
        "ingest_complete",
        dataset_id=dataset_id, object_key=storage_key,
        rows=table.num_rows, columns=table.num_columns,
    )
    return {
        "dataset_id": dataset_id,
        "rows": table.num_rows,
        "columns": table.num_columns,
        "schema": fields,
        "storage_key": storage_key,
        "format": "parquet+fernet",
    }


@app.post("/v1/query")
def query(payload: QueryRequest, x_worker_token: str = Header(default="")):
    if x_worker_token != settings.worker_token:
        raise HTTPException(401, "Invalid worker token")
    from .crypto import decrypt

    dataset_id = _validate_dataset_id(payload.dataset_id)
    # filter_count and restriction_count are COUNTS, never the columns or
    # values they carry -- see the privacy-constraint comment in
    # app/config.py and executor.py's module docstring.
    logger.info(
        "query_request_received",
        dataset_id=dataset_id, query_type=payload.query_type, epsilon=payload.epsilon,
        mechanism=payload.mechanism, filter_count=len(payload.filters),
        restriction_count=len(payload.row_restrictions),
    )
    # Mechanism/delta consistency and the Gaussian-mean restriction are
    # enforced authoritatively inside execute() (see its module docstring
    # and the checks near the top of execute()) -- not duplicated here, the
    # same way execute()'s bound-validity and query-shape checks are not
    # duplicated here either. Both depend only on public request metadata,
    # so raising from execute() rather than main.py still respects the
    # data-independence invariant; a ValueError from execute() below is
    # already mapped to a 400 by the except clause around that call.

    # Re-check the query against the dataset schema and policy before touching
    # the file.  The API already ran these checks; repeating them here means a
    # bug or a compromise on that side cannot turn a forbidden column into a
    # release.  Runs before decryption so a rejected query never loads data.
    if payload.columns:
        try:
            _validate_against_policy(payload)
        except ValidationError as error:
            # Safe to log: query_ir.validator messages only ever name a
            # column, a public bound, or an epsilon figure -- never a row or
            # filter value. (Row restrictions are never given to this
            # validator in the first place -- see _validate_against_policy.)
            logger.warning(
                "validation_rejected",
                dataset_id=dataset_id, check="policy", reason=str(error),
            )
            raise HTTPException(400, str(error)) from error

    storage_key = object_key_for_dataset(dataset_id)
    temp = tempfile.mkdtemp()
    try:
        path = Path(temp) / "data.parquet"
        try:
            encrypted = get(storage_key)
            decrypted = decrypt(encrypted, settings.encryption_key)
            path.write_bytes(decrypted)
            logger.info(
                "decryption_succeeded",
                dataset_id=dataset_id, object_key=storage_key, byte_size=len(decrypted),
            )
        except FileNotFoundError as error:
            logger.warning(
                "decryption_failed",
                dataset_id=dataset_id, object_key=storage_key, reason="object_not_found",
            )
            raise HTTPException(404, "Encrypted dataset object was not found") from error
        except ValueError as error:
            logger.warning(
                "decryption_failed",
                dataset_id=dataset_id, object_key=storage_key, reason="invalid_ciphertext_or_key",
            )
            raise HTTPException(400, str(error)) from error
        except Exception as error:
            # Missing local file or remote download failures surface as 404/400.
            # Log only the exception class, not its message -- a storage-backend
            # error message could echo the object key's full remote path.
            message = str(error)
            logger.warning(
                "decryption_failed",
                dataset_id=dataset_id, object_key=storage_key, reason=error.__class__.__name__,
            )
            if "No such file" in message or "not found" in message.lower():
                raise HTTPException(404, "Encrypted dataset object was not found") from error
            raise HTTPException(400, "Could not load encrypted dataset object") from error

        started = time.perf_counter()
        try:
            result = execute(
                path,
                payload.query_type,
                payload.epsilon,
                payload.group_by,
                payload.value_column,
                payload.lower,
                payload.upper,
                payload.bins,
                [item.model_dump() for item in payload.filters],
                payload.min_group_size,
                payload.max_groups,
                payload.top_k,
                payload.minimum_denominator,
                payload.group_categories,
                mechanism=payload.mechanism,
                delta=payload.delta,
                entity_column=payload.entity_column,
                max_contributions=payload.max_contributions,
                row_restrictions=[item.model_dump() for item in payload.row_restrictions],
            )
        except ValueError as error:
            # executor.py's ValueErrors are all static, policy-shaped messages
            # (missing bounds, disallowed mechanism, an invalid row
            # restriction, ...) -- never data-derived, so reason=str(error)
            # is safe to log even for the row-restriction failure path.
            logger.warning(
                "execution_rejected",
                dataset_id=dataset_id, query_type=payload.query_type, reason=str(error),
            )
            raise HTTPException(400, str(error)) from error
        duration_ms = (time.perf_counter() - started) * 1000
        # Log the mechanism name only -- never `result`'s value/values/buckets,
        # which carry the released (already-noised) figures.
        logger.info(
            "execution_complete",
            dataset_id=dataset_id, query_type=payload.query_type,
            duration_ms=round(duration_ms, 2), mechanism=result.get("mechanism"),
        )
        return result
    finally:
        _cleanup_temp(temp, dataset_id)


@app.post("/v1/storage/delete")
def delete_storage(payload: DeleteRequest, x_worker_token: str = Header(default="")):
    if x_worker_token != settings.worker_token:
        raise HTTPException(401, "Invalid worker token")
    dataset_id = _validate_dataset_id(payload.dataset_id)
    object_key = object_key_for_dataset(dataset_id)
    remove(object_key)
    logger.info("storage_delete_complete", dataset_id=dataset_id, object_key=object_key)
    return {"deleted": True, "dataset_id": dataset_id}
