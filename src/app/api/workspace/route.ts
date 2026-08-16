import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { filterDatasetsForRole, grantCreatorDatasetAccess, hasImplicitDatasetAccess } from "@/lib/dataset-auth";

export async function GET() {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const requestHeaders = await (await import("next/headers")).headers();
  const selectedOrganization = requestHeaders.get("x-organization-id");
  const selectedDataset = requestHeaders.get("x-dataset-id");

  let membershipQuery = supabase
    .from("organization_members")
    .select("organization_id,role")
    .eq("user_id", user.id);
  if (selectedOrganization) membershipQuery = membershipQuery.eq("organization_id", selectedOrganization);
  // Deterministic fallback when no organization is selected, so a multi-org
  // user does not land in a different workspace on each request.
  const { data: memberships } = await membershipQuery.order("created_at", { ascending: true }).limit(1);
  const membership = memberships?.[0];
  if (!membership) return NextResponse.json({ error: "Workspace not initialized" }, { status: 409 });

  const admin = createAdminClient();
  const { data: allDatasets } = await admin
    .from("datasets")
    .select("id,name,description,row_count,column_count,status,updated_at,file_format,revoked_at,created_by")
    .eq("organization_id", membership.organization_id)
    .order("created_at", { ascending: false });

  const active = (allDatasets || []).filter((item) => !item.revoked_at);

  // Repair missing creator grants for older seeds (owner/admin only).
  if (hasImplicitDatasetAccess(membership.role)) {
    for (const item of active) {
      if (item.created_by === user.id) {
        await grantCreatorDatasetAccess(admin, item.id, user.id).catch(() => undefined);
      }
    }
  }

  const allowedIds = new Set(
    await filterDatasetsForRole(admin, active, user.id, membership.role),
  );
  const datasets = hasImplicitDatasetAccess(membership.role)
    ? active
    : active.filter((item) => allowedIds.has(item.id));

  const dataset = datasets.find((item) => item.id === selectedDataset) || datasets[0];
  if (!dataset) {
    return NextResponse.json({
      error: hasImplicitDatasetAccess(membership.role)
        ? "Dataset unavailable"
        : "No datasets are available for your account. Ask an owner to grant run_queries access.",
    }, { status: 409 });
  }

  const [{ data: policy, error: policyError }, { data: columns }, { data: ledger }] = await Promise.all([
    supabase
      .from("privacy_policies")
      .select("epsilon_total,epsilon_used,delta_total,delta_used,min_group_size,allowed_query_types,max_groups,privacy_unit,public_min_denominator,entity_column,max_contributions,public_categories")
      .eq("dataset_id", dataset.id)
      .single(),
    supabase
      .from("dataset_columns")
      .select("name,data_type,can_group,can_measure,lower_bound,upper_bound,is_sensitive")
      .eq("dataset_id", dataset.id)
      .order("name"),
    supabase
      .from("privacy_ledger")
      .select("id,operation,epsilon_spent,created_at,query_id")
      .eq("dataset_id", dataset.id)
      .order("created_at", { ascending: false })
      .limit(30),
  ]);

  if (!policy) {
    // A missing ROW and a failed SELECT are different faults with different
    // remedies, and conflating them sends the operator somewhere useless.
    // PostgREST rejects the whole select when it names a column the database
    // does not have, which is exactly what an un-applied migration looks
    // like -- and no amount of signing out will fix that.
    const missingColumn = policyError?.code === "42703"
      || /column .* does not exist/i.test(policyError?.message ?? "");
    if (policyError) {
      return NextResponse.json({
        error: missingColumn
          ? "The database schema is behind this build: the privacy policy table is missing columns this version expects. Apply the outstanding migrations in supabase/ (see the Migrations section of README.md), then reload."
          : `The dataset privacy policy could not be read: ${policyError.message}`,
      }, { status: 409 });
    }
    return NextResponse.json({
      error: "Dataset privacy policy is missing. Sign out and sign in again to repair this workspace.",
    }, { status: 409 });
  }

  // A grouped release is refused unless the owner declared a public category
  // domain for the grouping column, so that -- not can_group -- is the real
  // precondition. can_group only says the column's TYPE permits grouping,
  // which is why identifier columns like patient_nbr and encounter_id were
  // being offered in the picker despite having 71,518 distinct values and no
  // declared domain. Only the boolean crosses the boundary: the category
  // labels themselves are public by definition, but nothing here needs them,
  // and the narrower response is the one that stays correct if that changes.
  const { public_categories, ...policyForClient } = policy as typeof policy & {
    public_categories: Record<string, string[]> | null;
  };
  const declaredDomains = public_categories ?? {};
  const columnsForClient = (columns || []).map((column) => ({
    ...column,
    has_public_domain: Array.isArray(declaredDomains[column.name])
      && declaredDomains[column.name].length > 0,
  }));

  return NextResponse.json({
    organizationId: membership.organization_id,
    user: {
      email: user.email,
      displayName: user.user_metadata.display_name || user.email?.split("@")[0],
      role: membership.role,
    },
    dataset,
    datasets,
    policy: policyForClient,
    columns: columnsForClient,
    ledger: ledger || [],
  });
}
