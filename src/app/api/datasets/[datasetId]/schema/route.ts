import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { userHasDatasetPermission } from "@/lib/dataset-auth";

interface RouteContext {
  params: Promise<{ datasetId: string }>;
}

const NUMERIC_TYPE_PREFIXES = ["int", "double", "float", "decimal", "numeric"];

/** A column counts as numeric if its stored type says so, independent of can_measure. */
function columnKind(dataType: string): "numeric" | "categorical" {
  const normalized = (dataType || "").toLowerCase();
  return NUMERIC_TYPE_PREFIXES.some((prefix) => normalized.startsWith(prefix)) ? "numeric" : "categorical";
}

/** Query types this column can participate in, given its capabilities and the dataset's enabled operations. */
function allowedOperations(
  column: { can_group: boolean; can_measure: boolean; lower_bound: number | null; upper_bound: number | null; is_sensitive: boolean },
  allowedQueryTypes: string[],
  hasPublicDomain: boolean,
): string[] {
  const ops: string[] = [];
  // Sensitive columns may still be measured below, but never grouped -- a
  // group label reveals the attribute directly. See migration-008.
  // hasPublicDomain is the other half: a grouped release is refused outright
  // without a declared category domain, so listing grouped_count for such a
  // column advertised an operation the query route always rejects.
  if (column.can_group && !column.is_sensitive && hasPublicDomain) {
    if (allowedQueryTypes.includes("grouped_count")) ops.push("grouped_count");
    if (allowedQueryTypes.includes("top_k")) ops.push("top_k");
  }
  if (column.can_measure && column.lower_bound != null && column.upper_bound != null) {
    if (allowedQueryTypes.includes("mean")) ops.push("mean");
    if (allowedQueryTypes.includes("bounded_sum")) ops.push("bounded_sum");
    if (allowedQueryTypes.includes("histogram")) ops.push("histogram");
  }
  return ops;
}

/**
 * GET /api/datasets/[datasetId]/schema
 * Returns per-column schema metadata (type, grouping/measurement capability,
 * public bounds, allowed operations) plus the dataset's privacy unit.
 * Never returns raw rows or per-group counts -- this is metadata only.
 */
export async function GET(_request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const admin = createAdminClient();

  const { data: dataset } = await admin
    .from("datasets")
    .select("id,organization_id,revoked_at")
    .eq("id", datasetId)
    .maybeSingle();

  if (!dataset) {
    return NextResponse.json({ error: "Dataset not found" }, { status: 404 });
  }

  if (dataset.revoked_at) {
    return NextResponse.json({ error: "This dataset has been revoked and no longer accepts queries." }, { status: 410 });
  }

  const { data: membership } = await admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", dataset.organization_id)
    .eq("user_id", user.id)
    .maybeSingle();

  if (!membership) {
    return NextResponse.json({ error: "Organization membership required" }, { status: 403 });
  }

  const canView = await userHasDatasetPermission(
    admin, datasetId, user.id, membership.role, "view_schema",
  );
  if (!canView) {
    return NextResponse.json({ error: "You do not have permission to view the schema for this dataset." }, { status: 403 });
  }

  const [{ data: policy }, { data: columns }] = await Promise.all([
    admin
      .from("privacy_policies")
      .select("privacy_unit,allowed_query_types,public_categories")
      .eq("dataset_id", datasetId)
      .single(),
    admin
      .from("dataset_columns")
      .select("name,data_type,can_group,can_measure,lower_bound,upper_bound,is_sensitive")
      .eq("dataset_id", datasetId)
      .order("name"),
  ]);

  if (!policy) {
    return NextResponse.json({ error: "Dataset privacy policy is missing." }, { status: 409 });
  }

  const allowedQueryTypes = policy.allowed_query_types || [];
  // Only whether a domain exists reaches the response, never the labels.
  const declaredDomains = (policy.public_categories ?? {}) as Record<string, string[]>;
  const hasPublicDomain = (name: string) =>
    Array.isArray(declaredDomains[name]) && declaredDomains[name].length > 0;

  return NextResponse.json({
    datasetId,
    privacyUnit: policy.privacy_unit,
    columns: (columns || []).map((column) => ({
      name: column.name,
      data_type: column.data_type,
      kind: columnKind(column.data_type),
      can_group: column.can_group,
      can_measure: column.can_measure,
      is_sensitive: column.is_sensitive,
      bounds: column.can_measure && column.lower_bound != null && column.upper_bound != null
        ? { lower: column.lower_bound, upper: column.upper_bound }
        : null,
      allowed_operations: allowedOperations(column, allowedQueryTypes, hasPublicDomain(column.name)),
    })),
  });
}
