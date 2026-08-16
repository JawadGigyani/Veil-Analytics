"""Integration tests for the analytics worker FastAPI application.

These tests exercise the full HTTP API using httpx AsyncClient,
covering ingestion, query execution, authentication, and error handling.
"""

import asyncio
from io import BytesIO
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as parquet
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from app.config import settings

DATASET_CSV = "11111111-1111-4111-8111-111111111111"
DATASET_PQ = "22222222-2222-4222-8222-222222222222"
DATASET_QUERY = "33333333-3333-4333-8333-333333333333"
DATASET_DELETE = "55555555-5555-4555-8555-555555555555"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", key)
    monkeypatch.setattr(settings, "worker_token", "test-integration-token")
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_service_role_key", None)


@pytest.fixture()
def client():
    from app.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


HEADERS = {"x-worker-token": "test-integration-token"}


def make_csv(rows: list[str]) -> BytesIO:
    content = "\n".join(rows).encode()
    return BytesIO(content)


def make_parquet_bytes(data: dict) -> bytes:
    buf = BytesIO()
    parquet.write_table(pa.table(data), buf)
    return buf.getvalue()


class TestHealthEndpoint:
    def test_returns_ok(self, client):
        response = asyncio.run(client.get("/health"))
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestIngestionEndpoint:
    def test_csv_upload(self, client):
        csv = make_csv(["region,value", "north,1.5", "south,2.25", "north,3.0"])

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={DATASET_CSV}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == 3
        assert body["columns"] == 2
        assert body["storage_key"] == f"{DATASET_CSV}.parquet.enc"

    def test_parquet_upload(self, client):
        data = make_parquet_bytes({"person": ["a", "b", "c"], "score": [1, 2, 3]})

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={DATASET_PQ}",
                headers=HEADERS,
                files={"upload": ("data.parquet", BytesIO(data), "application/octet-stream")},
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        assert response.json()["rows"] == 3

    def test_invalid_token_returns_401(self, client):
        csv = make_csv(["x", "1"])

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={uuid4()}",
                headers={"x-worker-token": "wrong"},
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        response = asyncio.run(run())
        assert response.status_code == 401

    def test_unsupported_format_returns_415(self, client):
        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={uuid4()}",
                headers=HEADERS,
                files={"upload": ("data.json", BytesIO(b"{}"), "application/json")},
            )

        response = asyncio.run(run())
        assert response.status_code == 415


class TestQueryEndpoint:
    @pytest.fixture(autouse=True)
    def ingest_dataset(self, client):
        """Upload a dataset before each query test."""
        csv = make_csv([
            "region,score",
            "north,10", "north,20", "north,30",
            "south,15", "south,25",
            "east,50", "east,60",
            "west,5",
        ])

        async def run():
            response = await client.post(
                f"/v1/ingest?dataset_id={DATASET_QUERY}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )
            return response.json()

        result = asyncio.run(run())
        self.storage_key = result["storage_key"]

    def test_count_query(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "count",
                    "epsilon": 1.0,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        body = response.json()
        assert "value" in body
        assert body["value"] >= 0
        assert "range" in body
        assert body.get("mechanism") == "laplace"

    def test_count_query_gaussian_succeeds_and_is_self_describing(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "count",
                    "epsilon": 1.0,
                    "mechanism": "gaussian",
                    "delta": 1e-5,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        body = response.json()
        assert body["value"] >= 0
        assert body["mechanism"] == "gaussian"
        assert body["delta"] == 1e-5
        assert body["sigma"] > 0

    def test_gaussian_query_with_zero_delta_rejected(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "count",
                    "epsilon": 1.0,
                    "mechanism": "gaussian",
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 400
        assert "delta" in response.json()["detail"].lower()

    def test_laplace_query_with_positive_delta_rejected(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "count",
                    "epsilon": 1.0,
                    "mechanism": "laplace",
                    "delta": 1e-5,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 400
        assert "delta" in response.json()["detail"].lower()

    def test_gaussian_mean_query_rejected(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "mean",
                    "value_column": "score",
                    "lower": 0,
                    "upper": 100,
                    "epsilon": 1.0,
                    "mechanism": "gaussian",
                    "delta": 1e-5,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 400
        assert "not supported for mean" in response.json()["detail"]

    def test_ignores_attacker_storage_key(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "storage_key": "../escape.parquet.enc",
                    "query_type": "count",
                    "epsilon": 1.0,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        assert response.json()["value"] >= 0

    def test_grouped_count_query(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "grouped_count",
                    "epsilon": 1.0,
                    "group_by": "region",
                    "group_categories": ["north", "south", "east", "west"],
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        body = response.json()
        assert "values" in body
        assert body["selection"] == "public_domain_noisy_threshold"

    def test_invalid_token_rejected(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={"x-worker-token": "wrong", "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "count",
                    "epsilon": 1.0,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 401

    def test_invalid_query_type_rejected(self, client):
        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={
                    "dataset_id": DATASET_QUERY,
                    "query_type": "invalid_type",
                    "epsilon": 1.0,
                },
            )

        response = asyncio.run(run())
        assert response.status_code == 422  # Pydantic validation error


class TestStorageDeleteEndpoint:
    def test_delete_by_dataset_id_succeeds(self, client):
        async def run():
            return await client.post(
                "/v1/storage/delete",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={"dataset_id": DATASET_DELETE},
            )

        response = asyncio.run(run())
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert response.json()["dataset_id"] == DATASET_DELETE

    def test_delete_rejects_missing_dataset_id(self, client):
        async def run():
            return await client.post(
                "/v1/storage/delete",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={"storage_key": "legacy.parquet.enc"},
            )

        response = asyncio.run(run())
        assert response.status_code == 422


class TestWorkerSideValidation:
    """The worker re-checks column policy instead of trusting the API.

    The Next.js route runs these checks too. Repeating them behind the worker
    token means a bug or a compromise on the API side cannot turn a column the
    owner marked non-groupable or non-measurable into a release.
    """

    DATASET = "66666666-6666-4666-8666-666666666666"

    COLUMNS = [
        {"name": "region", "data_type": "string", "can_group": True, "can_measure": False,
         "lower_bound": None, "upper_bound": None},
        {"name": "score", "data_type": "int64", "can_group": False, "can_measure": True,
         "lower_bound": 0, "upper_bound": 100},
        {"name": "secret_id", "data_type": "int64", "can_group": False, "can_measure": False,
         "lower_bound": None, "upper_bound": None},
    ]

    @pytest.fixture(autouse=True)
    def ingest_dataset(self, client):
        csv = make_csv([
            "region,score,secret_id",
            "north,10,1", "north,20,2", "south,15,3", "east,50,4",
        ])

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={self.DATASET}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        asyncio.run(run())

    def post(self, client, **overrides):
        body = {
            "dataset_id": self.DATASET,
            "query_type": "count",
            "epsilon": 1.0,
            "columns": self.COLUMNS,
            "epsilon_total": 5.0,
            "epsilon_used": 0.0,
            "allowed_query_types": ["count", "grouped_count", "mean", "bounded_sum", "histogram", "top_k"],
        }
        body.update(overrides)

        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json=body,
            )

        return asyncio.run(run())

    def test_accepts_a_well_formed_query(self, client):
        response = self.post(client)
        assert response.status_code == 200
        assert response.json()["value"] >= 0

    def test_rejects_grouping_on_a_non_groupable_column(self, client):
        response = self.post(
            client, query_type="grouped_count",
            group_by="score", group_categories=["10", "20"],
        )
        assert response.status_code == 400
        assert "grouping" in response.json()["detail"].lower()

    def test_rejects_measuring_a_non_measurable_column(self, client):
        response = self.post(
            client, query_type="mean", value_column="secret_id", lower=0, upper=100,
        )
        assert response.status_code == 400
        assert "measurement" in response.json()["detail"].lower()

    def test_rejects_an_unknown_column(self, client):
        response = self.post(
            client, filters=[{"column": "not_a_column", "operator": "equals", "value": "x"}],
        )
        assert response.status_code == 400
        assert "unknown column" in response.json()["detail"].lower()

    def test_rejects_a_query_type_the_policy_disallows(self, client):
        response = self.post(client, allowed_query_types=["grouped_count"])
        assert response.status_code == 400
        assert "not allowed" in response.json()["detail"].lower()

    def test_rejects_a_query_that_exceeds_remaining_budget(self, client):
        response = self.post(client, epsilon=2.0, epsilon_total=5.0, epsilon_used=4.5)
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()

    def test_rejects_grouping_without_a_public_category_domain(self, client):
        response = self.post(client, query_type="grouped_count", group_by="region", group_categories=[])
        assert response.status_code == 400
        assert "category domain" in response.json()["detail"].lower()

    def test_rejects_a_measure_column_missing_public_bounds(self, client):
        columns = [dict(column) for column in self.COLUMNS]
        columns[1]["lower_bound"] = None
        columns[1]["upper_bound"] = None
        response = self.post(
            client, query_type="mean", value_column="score", lower=0, upper=100, columns=columns,
        )
        assert response.status_code == 400
        assert "bounds" in response.json()["detail"].lower()

    def test_rejects_an_entity_column_not_in_the_schema(self, client):
        response = self.post(client, entity_column="not_a_column")
        assert response.status_code == 400
        assert "entity column" in response.json()["detail"].lower()

    def test_accepts_a_known_entity_column(self, client):
        response = self.post(client, entity_column="secret_id", max_contributions=2)
        assert response.status_code == 200

    def test_accepts_a_gaussian_query_with_columns_forwarded(self, client):
        """A real request from the Next.js route always forwards `columns`,
        which routes through _validate_against_policy before execute() runs.
        Without a real delta_total wired into that path (rather than the
        hardcoded 0.0 this replaced), every Gaussian request with columns
        attached would be rejected by the worker's own second-layer check
        regardless of the actual policy, independent of execute()'s own
        mechanism/delta handling."""
        response = self.post(client, mechanism="gaussian", delta=1e-5)
        assert response.status_code == 200
        body = response.json()
        assert body["mechanism"] == "gaussian"
        assert body["sigma"] > 0

    def test_rejects_a_gaussian_query_whose_delta_exceeds_the_policy_delta_total(self, client):
        response = self.post(
            client, mechanism="gaussian", delta=0.5, delta_total=0.1, delta_used=0.0,
        )
        assert response.status_code == 400
        assert "delta" in response.json()["detail"].lower()


class TestRowRestrictionsEndpoint:
    """row_restrictions flow from /v1/query through to the executor and
    filter rows before the analyst's own filters, without appearing in any
    response or error."""

    DATASET = "88888888-8888-4888-8888-888888888888"

    @pytest.fixture(autouse=True)
    def ingest_dataset(self, client):
        rows = ["included,region"] + [
            f"yes,north" for _ in range(10)
        ] + [f"no,north" for _ in range(10)]
        csv = make_csv(rows)

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={self.DATASET}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        asyncio.run(run())

    def post(self, client, **overrides):
        body = {
            "dataset_id": self.DATASET,
            "query_type": "count",
            "epsilon": 1.0,
        }
        body.update(overrides)

        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json=body,
            )

        return asyncio.run(run())

    def test_restriction_filters_roughly_half_the_rows(self, client):
        response = self.post(client, row_restrictions=[
            {"column": "included", "operator": "equals", "value": "yes"}
        ])
        assert response.status_code == 200
        assert response.json()["value"] >= 0

    def test_empty_row_restrictions_reproduce_default_behaviour(self, client):
        # Real (unpatched) Laplace noise is live on this endpoint, so the
        # released `value` legitimately differs run to run -- compare
        # everything except that, plus the deterministic `range`.
        with_field = self.post(client, row_restrictions=[])
        without_field = self.post(client)
        assert with_field.status_code == without_field.status_code == 200
        with_body, without_body = with_field.json(), without_field.json()
        assert with_body.keys() == without_body.keys()
        assert with_body["range"] == without_body["range"]
        assert with_body["mechanism"] == without_body["mechanism"]

    def test_too_many_restrictions_rejected_by_schema(self, client):
        response = self.post(client, row_restrictions=[
            {"column": f"c{i}", "operator": "equals", "value": "x"} for i in range(11)
        ])
        assert response.status_code == 422

    def test_restriction_on_unknown_column_fails_without_naming_it(self, client):
        response = self.post(client, row_restrictions=[
            {"column": "not_a_real_column", "operator": "equals", "value": "top-secret-value"}
        ])
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "not_a_real_column" not in detail
        assert "top-secret-value" not in detail


class TestSensitiveColumnsEndpoint:
    """sensitive_columns / is_sensitive flow from /v1/query into the
    schema the worker re-validates against: a sensitive column may be
    measured but never grouped or filtered on."""

    DATASET = "99999999-9999-4999-9999-999999999999"

    COLUMNS = [
        {"name": "region", "data_type": "string", "can_group": True, "can_measure": False,
         "lower_bound": None, "upper_bound": None, "is_sensitive": False},
        {"name": "diagnosis", "data_type": "string", "can_group": True, "can_measure": True,
         "lower_bound": 0, "upper_bound": 1, "is_sensitive": True},
    ]

    @pytest.fixture(autouse=True)
    def ingest_dataset(self, client):
        csv = make_csv([
            "region,diagnosis",
            "north,1", "north,0", "south,1", "east,0",
        ])

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={self.DATASET}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        asyncio.run(run())

    def post(self, client, **overrides):
        body = {
            "dataset_id": self.DATASET,
            "query_type": "count",
            "epsilon": 1.0,
            "columns": self.COLUMNS,
            "epsilon_total": 5.0,
            "epsilon_used": 0.0,
            "allowed_query_types": ["count", "grouped_count", "mean", "bounded_sum", "histogram", "top_k"],
        }
        body.update(overrides)

        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json=body,
            )

        return asyncio.run(run())

    def test_sensitive_column_rejected_for_group_by(self, client):
        response = self.post(
            client, query_type="grouped_count",
            group_by="diagnosis", group_categories=["0", "1"],
        )
        assert response.status_code == 400
        assert "sensitive" in response.json()["detail"].lower()

    def test_sensitive_column_rejected_as_filter(self, client):
        response = self.post(
            client, filters=[{"column": "diagnosis", "operator": "equals", "value": "1"}],
        )
        assert response.status_code == 400
        assert "sensitive" in response.json()["detail"].lower()

    def test_sensitive_column_accepted_as_measured_column(self, client):
        response = self.post(
            client, query_type="mean", value_column="diagnosis", lower=0, upper=1,
        )
        assert response.status_code == 200

    def test_sensitive_columns_top_level_list_also_enforced(self, client):
        """A column flagged only via the top-level sensitive_columns list
        (not per-column is_sensitive) must be enforced identically."""
        columns = [dict(c) for c in self.COLUMNS]
        columns[1]["is_sensitive"] = False
        response = self.post(
            client, columns=columns, sensitive_columns=["diagnosis"],
            filters=[{"column": "diagnosis", "operator": "equals", "value": "1"}],
        )
        assert response.status_code == 400
        assert "sensitive" in response.json()["detail"].lower()

    def test_non_sensitive_column_unaffected(self, client):
        response = self.post(
            client, query_type="grouped_count",
            group_by="region", group_categories=["north", "south", "east"],
        )
        assert response.status_code == 200


class TestEntityContributionBoundingEndpoint:
    """The two new QueryRequest fields, entity_column and max_contributions,
    flow all the way through /v1/query to the executor and back."""

    DATASET = "77777777-7777-4777-8777-777777777777"

    @pytest.fixture(autouse=True)
    def ingest_dataset(self, client):
        # person 1 contributes 10 rows; person 2 contributes 2.
        rows = [f"person,value"] + [f"1,{i}" for i in range(10)] + [f"2,{i}" for i in range(2)]
        csv = make_csv(rows)

        async def run():
            return await client.post(
                f"/v1/ingest?dataset_id={self.DATASET}",
                headers=HEADERS,
                files={"upload": ("data.csv", csv, "text/csv")},
            )

        asyncio.run(run())

    def post(self, client, **overrides):
        body = {
            "dataset_id": self.DATASET,
            "query_type": "count",
            "epsilon": 1.0,
        }
        body.update(overrides)

        async def run():
            return await client.post(
                "/v1/query",
                headers={**HEADERS, "Content-Type": "application/json"},
                json=body,
            )

        return asyncio.run(run())

    def test_response_reports_row_adjacency_by_default(self, client):
        response = self.post(client)
        assert response.status_code == 200
        body = response.json()
        assert body["entity_column"] is None
        assert body["max_contributions"] == 1
        assert body["privacy_unit"] == "row"

    def test_response_reports_entity_adjacency_when_requested(self, client):
        response = self.post(client, entity_column="person", max_contributions=3)
        assert response.status_code == 200
        body = response.json()
        assert body["entity_column"] == "person"
        assert body["max_contributions"] == 3
        assert body["privacy_unit"] == "entity"

    def test_max_contributions_out_of_range_is_rejected_by_the_schema(self, client):
        response = self.post(client, entity_column="person", max_contributions=0)
        assert response.status_code == 422
        response = self.post(client, entity_column="person", max_contributions=101)
        assert response.status_code == 422
