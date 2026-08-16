from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    storage_root: Path = Path(".veil-storage")
    encryption_key: str
    max_upload_bytes: int = 25_000_000
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    storage_bucket: str = "protected-datasets"
    worker_token: str
    # Overridable via the LOG_LEVEL env var. Standard library level names
    # ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL").
    log_level: str = "INFO"

settings = Settings()

# --- Logging setup ----------------------------------------------------------
# This is the single place logging is configured for the whole worker; every
# module does `logger = structlog.get_logger(__name__)` and relies on this
# having already run, since `app.config` is imported before anything else.
#
# CRITICAL PRIVACY CONSTRAINT: this service decrypts protected data into a
# temp directory and executes analyst queries against it. Log records must
# NEVER contain row values, filter values, group/category values,
# row-restriction content (column or value -- see executor.py's module
# docstring), released results (noisy or true counts), the encryption key,
# or the worker token. Log the column NAME and the NUMBER of filters, never
# a filter's VALUE -- a predicate like `diagnosis = "HIV"` is itself
# disclosive. This mirrors the rule already enforced for audit events in
# `src/lib/audit.ts`. Do not add a log call that interpolates a data value
# to satisfy a future debugging need.
#
# Why structlog makes this easier to keep true: every call site is
# `logger.info("event_name", key=value, ...)` -- explicit keyword arguments,
# not a message string a value gets silently interpolated into. A future
# change that widens what a log call carries has to add a new, visible
# `key=...` argument at the call site, which a reviewer can check against
# this constraint directly; there is no f-string or %-format hiding a value
# inside prose the way `f"query for {row['diagnosis']}"` could. This is the
# whole reason the migration off stdlib `logging` is worth doing.
_level = getattr(logging, settings.log_level.upper(), logging.INFO)

# Route stdlib logging (third-party libraries) through the same level so a
# noisy dependency cannot bypass LOG_LEVEL, but leave rendering to structlog.
logging.basicConfig(format="%(message)s", stream=sys.stdout, level=_level)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        # ISO 8601 timestamps, e.g. "2026-08-16T12:00:00.000000Z".
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_level),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    cache_logger_on_first_use=True,
)
