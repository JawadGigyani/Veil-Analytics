"""Structlog migration tests.

The privacy constraint in app/config.py says log records must never carry a
row value, filter value, group/category value, or row-restriction content.
These tests run real requests through the FastAPI app -- with a planted
sentinel used as a filter value, a group category, and a row-restriction
value -- and assert the sentinel never reaches any emitted log event, in
any key or value.

``structlog.testing.capture_logs`` replaces the configured processor chain
(JSON rendering, timestamps, ...) with a capture that appends the raw event
dict for every log call made while the context is active, across every
module's logger. That is a stronger check than scraping rendered stdout: it
inspects the literal keyword arguments passed to each ``logger.*()`` call.
"""

import asyncio
from io import BytesIO

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from app.config import settings

SENTINEL = "sentinel-9f3d2b7e-do-not-log-me"

DATASET = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", key)
    monkeypatch.setattr(settings, "worker_token", "test-logging-token")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_service_role_key", None)


@pytest.fixture()
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


HEADERS = {"x-worker-token": "test-logging-token", "Content-Type": "application/json"}


def make_csv(rows: list[str]) -> BytesIO:
    return BytesIO("\n".join(rows).encode())


def ingest(client, dataset_id: str):
    csv = make_csv([
        "region,included",
        "north,yes", "south,no", "north,yes", "east,no",
    ])

    async def run():
        return await client.post(
            f"/v1/ingest?dataset_id={dataset_id}",
            headers={"x-worker-token": "test-logging-token"},
            files={"upload": ("data.csv", csv, "text/csv")},
        )

    return asyncio.run(run())


def post_query(client, **body):
    async def run():
        return await client.post("/v1/query", headers=HEADERS, json=body)

    return asyncio.run(run())


def assert_sentinel_absent(entries):
    assert entries, "expected at least one captured log entry"
    for entry in entries:
        for key, value in entry.items():
            assert SENTINEL not in str(key), f"sentinel leaked in key {key!r} of {entry}"
            assert SENTINEL not in str(value), f"sentinel leaked in value of key {key!r}: {entry}"


class TestSentinelNeverLogged:
    def test_sentinel_as_filter_value_never_logged(self, client):
        ingest(client, DATASET)
        with capture_logs() as entries:
            response = post_query(
                client,
                dataset_id=DATASET,
                query_type="count",
                epsilon=1.0,
                filters=[{"column": "region", "operator": "equals", "value": SENTINEL}],
            )
        assert response.status_code == 200
        assert_sentinel_absent(entries)

    def test_sentinel_as_group_category_never_logged(self, client):
        ingest(client, DATASET)
        with capture_logs() as entries:
            response = post_query(
                client,
                dataset_id=DATASET,
                query_type="grouped_count",
                epsilon=1.0,
                group_by="region",
                group_categories=[SENTINEL, "north"],
            )
        assert response.status_code == 200
        assert_sentinel_absent(entries)

    def test_sentinel_as_row_restriction_value_never_logged(self, client):
        ingest(client, DATASET)
        with capture_logs() as entries:
            response = post_query(
                client,
                dataset_id=DATASET,
                query_type="count",
                epsilon=1.0,
                row_restrictions=[{"column": "included", "operator": "equals", "value": SENTINEL}],
            )
        assert response.status_code == 200
        assert_sentinel_absent(entries)

    def test_sentinel_as_row_restriction_column_never_logged_even_on_failure(self, client):
        """A restriction naming a column absent from the file fails with a
        static message (see executor.py's _reject_unknown_restriction_columns)
        -- confirm the sentinel used as the restriction's *column name* also
        never reaches a log line, including on the rejection path."""
        ingest(client, DATASET)
        with capture_logs() as entries:
            response = post_query(
                client,
                dataset_id=DATASET,
                query_type="count",
                epsilon=1.0,
                row_restrictions=[{"column": SENTINEL, "operator": "equals", "value": "x"}],
            )
        assert response.status_code == 400
        assert SENTINEL not in response.json()["detail"]
        assert_sentinel_absent(entries)

    def test_sentinel_used_in_all_three_places_at_once(self, client):
        ingest(client, DATASET)
        with capture_logs() as entries:
            response = post_query(
                client,
                dataset_id=DATASET,
                query_type="count",
                epsilon=1.0,
                filters=[{"column": "region", "operator": "equals", "value": SENTINEL}],
                row_restrictions=[{"column": "included", "operator": "equals", "value": SENTINEL}],
            )
        assert response.status_code == 200
        assert_sentinel_absent(entries)
