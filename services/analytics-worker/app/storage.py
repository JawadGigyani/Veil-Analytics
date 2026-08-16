from __future__ import annotations

import structlog
from pathlib import Path
from .config import settings

# See the privacy-constraint comment in app/config.py -- object keys are
# derived only from dataset ids, never from row content, so they are safe to
# log; the bytes they point at are not.
logger = structlog.get_logger(__name__)


def object_key_for_dataset(dataset_id: str) -> str:
    """Derive the only allowed storage object name for a dataset."""
    if not dataset_id or any(ch in dataset_id for ch in ("/", "\\", "..")):
        raise ValueError("Invalid dataset_id for storage key")
    # Keep UUID-like identifiers; reject path-like values.
    safe = dataset_id.strip()
    if not safe or safe != Path(safe).name:
        raise ValueError("Invalid dataset_id for storage key")
    return f"{safe}.parquet.enc"


def sanitize_key(key: str) -> str:
    """Reject path traversal and non-basename keys."""
    if not key or key != Path(key).name:
        raise ValueError("Invalid storage key")
    if any(part == ".." for part in Path(key).parts):
        raise ValueError("Invalid storage key")
    if "/" in key or "\\" in key:
        raise ValueError("Invalid storage key")
    return key


def _local_path(key: str) -> Path:
    safe = sanitize_key(key)
    root = settings.storage_root.resolve()
    path = (root / safe).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Storage path escapes storage root")
    return path


def put(key: str, data: bytes) -> str:
    safe = sanitize_key(key)
    backend = "supabase" if (settings.supabase_url and settings.supabase_service_role_key) else "local"
    if settings.supabase_url and settings.supabase_service_role_key:
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        client.storage.from_(settings.storage_bucket).upload(
            safe, data, {"content-type": "application/octet-stream", "upsert": "true"},
        )
        logger.info("storage_put_complete", object_key=safe, backend=backend, byte_size=len(data))
        return safe
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    _local_path(safe).write_bytes(data)
    logger.info("storage_put_complete", object_key=safe, backend=backend, byte_size=len(data))
    return safe


def get(key: str) -> bytes:
    safe = sanitize_key(key)
    backend = "supabase" if (settings.supabase_url and settings.supabase_service_role_key) else "local"
    if settings.supabase_url and settings.supabase_service_role_key:
        from supabase import create_client
        data = create_client(
            settings.supabase_url, settings.supabase_service_role_key,
        ).storage.from_(settings.storage_bucket).download(safe)
        logger.info("storage_get_complete", object_key=safe, backend=backend, byte_size=len(data))
        return data
    data = _local_path(safe).read_bytes()
    logger.info("storage_get_complete", object_key=safe, backend=backend, byte_size=len(data))
    return data


def remove(key: str) -> None:
    safe = sanitize_key(key)
    backend = "supabase" if (settings.supabase_url and settings.supabase_service_role_key) else "local"
    if settings.supabase_url and settings.supabase_service_role_key:
        from supabase import create_client
        create_client(
            settings.supabase_url, settings.supabase_service_role_key,
        ).storage.from_(settings.storage_bucket).remove([safe])
        logger.info("storage_remove_complete", object_key=safe, backend=backend)
        return
    _local_path(safe).unlink(missing_ok=True)
    logger.info("storage_remove_complete", object_key=safe, backend=backend)
