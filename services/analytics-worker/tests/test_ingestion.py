import asyncio
from io import BytesIO
from uuid import uuid4

import pyarrow.parquet as parquet
from cryptography.fernet import Fernet
from fastapi import UploadFile
import pytest

from app import main
from app.crypto import decrypt


def test_ingest_converts_csv_to_encrypted_parquet_in_local_storage(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    dataset_id = str(uuid4())
    monkeypatch.setattr(main.settings, "encryption_key", key)
    monkeypatch.setattr(main.settings, "worker_token", "test-worker-token")
    monkeypatch.setattr(main.settings, "storage_root", tmp_path)
    monkeypatch.setattr(main.settings, "supabase_url", None)
    monkeypatch.setattr(main.settings, "supabase_service_role_key", None)
    upload = UploadFile(
        filename="measurements.CSV",
        file=BytesIO(b"region,value\nnorth,1.5\nsouth,2.25\nnorth,3.0\n"),
    )

    result = asyncio.run(
        main.ingest(
            dataset_id=dataset_id,
            upload=upload,
            x_worker_token="test-worker-token",
        )
    )

    assert result == {
        "dataset_id": dataset_id,
        "rows": 3,
        "columns": 2,
        "schema": [
            {
                "name": "region",
                "data_type": "string",
                "can_measure": False,
                "can_group": True,
            },
            {
                "name": "value",
                "data_type": "double",
                "can_measure": True,
                "can_group": True,
            },
        ],
        "storage_key": f"{dataset_id}.parquet.enc",
        "format": "parquet+fernet",
    }

    encrypted = (tmp_path / result["storage_key"]).read_bytes()
    assert not encrypted.startswith(b"PAR1")
    table = parquet.read_table(BytesIO(decrypt(encrypted, key)))
    assert table.to_pydict() == {
        "region": ["north", "south", "north"],
        "value": [1.5, 2.25, 3.0],
    }


def test_ingest_accepts_parquet(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    dataset_id = str(uuid4())
    monkeypatch.setattr(main.settings, "encryption_key", key)
    monkeypatch.setattr(main.settings, "worker_token", "test-worker-token")
    monkeypatch.setattr(main.settings, "storage_root", tmp_path)
    monkeypatch.setattr(main.settings, "supabase_url", None)
    monkeypatch.setattr(main.settings, "supabase_service_role_key", None)
    source = BytesIO()
    parquet.write_table(__import__("pyarrow").table({"person": ["a", "b"], "value": [1, 2]}), source)
    upload = UploadFile(filename="input.parquet", file=BytesIO(source.getvalue()))

    result = asyncio.run(main.ingest(dataset_id, upload, "test-worker-token"))

    assert result["rows"] == 2
    assert result["columns"] == 2
    assert result["storage_key"] == f"{dataset_id}.parquet.enc"


def test_ingest_rejects_non_uuid_dataset_id(tmp_path, monkeypatch):
    from fastapi import HTTPException

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(main.settings, "encryption_key", key)
    monkeypatch.setattr(main.settings, "worker_token", "test-worker-token")
    monkeypatch.setattr(main.settings, "storage_root", tmp_path)
    monkeypatch.setattr(main.settings, "supabase_url", None)
    monkeypatch.setattr(main.settings, "supabase_service_role_key", None)
    upload = UploadFile(filename="data.csv", file=BytesIO(b"x\n1\n"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.ingest("not-a-uuid", upload, "test-worker-token"))
    assert exc.value.status_code == 400
