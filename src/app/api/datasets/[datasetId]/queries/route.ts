import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { userHasDatasetPermission } from "@/lib/dataset-auth";

interface RouteContext {
  params: Promise<{ datasetId: string }>;
}

/**
 * GET /api/datasets/[datasetId]/queries
 * Returns query history for the dataset.
 * Analysts see their own queries; owners/admins see all.
 */
export async function GET(request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const admin = createAdminClient();

  // Verify dataset exists and user has access
  const { data: dataset } = await admin
    .from("datasets")
    .select("id,organization_id")
    .eq("id", datasetId)
    .maybeSingle();

  if (!dataset) {
    return NextResponse.json({ error: "Dataset not found" }, { status: 404 });
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

  const canQuery = await userHasDatasetPermission(
    admin, datasetId, user.id, membership.role, "run_queries",
  );
  if (!canQuery) {
    return NextResponse.json({ error: "You do not have permission to view queries for this dataset." }, { status: 403 });
  }

  const url = new URL(request.url);
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50"), 100);
  const offset = parseInt(url.searchParams.get("offset") || "0");
  const status = url.searchParams.get("status");

  // Build query — analysts see only their own queries, admins/owners see all
  let query = admin
    .from("queries")
    .select("id,submitted_by,query_spec,status,epsilon_spent,released_result,error_message,finalized_at,created_at,normalized_query_hash")
    .eq("dataset_id", datasetId)
    .order("created_at", { ascending: false })
    .range(offset, offset + limit - 1);

  if (membership.role === "analyst") {
    query = query.eq("submitted_by", user.id);
  }

  if (status) {
    query = query.eq("status", status);
  }

  const { data: queries, error } = await query;

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  return NextResponse.json({
    queries: queries || [],
    limit,
    offset,
    hasMore: (queries || []).length === limit,
  });
}
