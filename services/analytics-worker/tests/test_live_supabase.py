import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.live,pytest.mark.skipif(not os.getenv("RUN_LIVE_SUPABASE_TESTS"),reason="Set RUN_LIVE_SUPABASE_TESTS=1 for live integration tests")]

def test_live_private_bucket_and_schema():
    from supabase import create_client
    url=os.environ["SUPABASE_URL"]; service=os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    client=create_client(url,service)
    buckets=client.storage.list_buckets()
    protected=next((bucket for bucket in buckets if bucket.name=="protected-datasets"),None)
    assert protected is not None
    assert protected.public is False
    for table in ("organizations","organization_members","datasets","dataset_columns","privacy_policies","queries","privacy_ledger","audit_events"):
        response=client.table(table).select("*").limit(1).execute()
        assert response.data is not None


def test_privacy_hardening_migration_and_browser_isolation():
    from postgrest.exceptions import APIError
    from supabase import create_client

    url=os.environ["SUPABASE_URL"]
    service=create_client(url,os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    browser=create_client(url,os.environ["SUPABASE_ANON_KEY"])

    policy=service.table("privacy_policies").select("allowed_query_types,max_groups,privacy_unit,public_min_denominator,public_categories").limit(1).execute()
    assert policy.data is not None
    assert browser.table("organizations").select("id").limit(1).execute().data == []

    arguments={
        "target_query":str(uuid4()),"target_dataset":str(uuid4()),"target_actor":str(uuid4()),
        "target_epsilon":-0.1,"target_spec":{"type":"count"},"target_operation":"count",
    }
    with pytest.raises(APIError):
        service.rpc("reserve_privacy_budget",arguments).execute()
    with pytest.raises(APIError):
        browser.rpc("reserve_privacy_budget",arguments).execute()
