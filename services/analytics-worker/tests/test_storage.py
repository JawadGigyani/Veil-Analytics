from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app import storage
from app.config import settings


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_service_role_key", None)
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


def test_object_key_for_dataset_uses_uuid_basename():
    dataset_id = "11111111-1111-1111-1111-111111111111"
    assert storage.object_key_for_dataset(dataset_id) == f"{dataset_id}.parquet.enc"


def test_object_key_rejects_path_segments():
    with pytest.raises(ValueError):
        storage.object_key_for_dataset("../escape")
    with pytest.raises(ValueError):
        storage.object_key_for_dataset("a/b")


def test_sanitize_key_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        storage.sanitize_key("../secret")
    with pytest.raises(ValueError):
        storage.sanitize_key("..\\secret")
    with pytest.raises(ValueError):
        storage.sanitize_key("nested/key.parquet.enc")


def test_put_get_roundtrip(tmp_path):
    key = "11111111-1111-1111-1111-111111111111.parquet.enc"
    storage.put(key, b"encrypted-bytes")
    assert storage.get(key) == b"encrypted-bytes"
    assert (Path(tmp_path) / key).exists()


def test_remove_missing_is_ok():
    storage.remove("22222222-2222-2222-2222-222222222222.parquet.enc")
