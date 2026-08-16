import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

interface RouteContext {
  params: Promise<{ datasetId: string }>;
}

async function resolveAdminContext(datasetId: string) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const admin = createAdminClient();
  const { data: dataset } = await admin
    .from("datasets")
    .select("id,organization_id")
    .eq("id", datasetId)
    .maybeSingle();

  if (!dataset) return null;

  const { data: membership } = await admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", dataset.organization_id)
    .eq("user_id", user.id)
    .maybeSingle();

  if (!membership || !["owner", "admin"].includes(membership.role)) return null;

  return { user, admin, dataset, role: membership.role };
}

/**
 * GET /api/datasets/[datasetId]/permissions
 * List all permissions for a dataset. Requires owner or admin.
 */
export async function GET(_request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveAdminContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });

  const { data: permissions, error } = await ctx.admin
    .from("dataset_permissions")
    .select("dataset_id,user_id,permission,granted_by,created_at")
    .eq("dataset_id", datasetId)
    .order("created_at", { ascending: false });

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  // Resolve user emails
  const userIds = [...new Set((permissions || []).flatMap(p => [p.user_id, p.granted_by].filter(Boolean)))];
  const userMap: Record<string, string> = {};
  for (const userId of userIds) {
    const { data } = await ctx.admin.auth.admin.getUserById(userId);
    if (data.user?.email) userMap[userId] = data.user.email;
  }

  const enriched = (permissions || []).map(p => ({
    ...p,
    user_email: p.user_id ? userMap[p.user_id] || null : null,
    granted_by_email: p.granted_by ? userMap[p.granted_by] || null : null,
  }));

  return NextResponse.json({ permissions: enriched });
}

/**
 * POST /api/datasets/[datasetId]/permissions
 * Grant a permission to a user. Requires owner or admin.
 */
export async function POST(request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveAdminContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });

  const body = await request.json().catch(() => null);
  if (!body) return NextResponse.json({ error: "Invalid request body" }, { status: 400 });

  const { userId, permission } = body;
  const validPermissions = ["view_schema", "run_queries", "manage_dataset", "view_audit_log"];

  if (!userId || typeof userId !== "string") {
    return NextResponse.json({ error: "userId is required" }, { status: 400 });
  }
  if (!validPermissions.includes(permission)) {
    return NextResponse.json({ error: `Invalid permission. Valid: ${validPermissions.join(", ")}` }, { status: 400 });
  }

  // Verify target user exists and is in the organization
  const { data: targetMember } = await ctx.admin
    .from("organization_members")
    .select("user_id")
    .eq("organization_id", ctx.dataset.organization_id)
    .eq("user_id", userId)
    .maybeSingle();

  if (!targetMember) {
    return NextResponse.json({ error: "User is not a member of this organization" }, { status: 404 });
  }

  const { error } = await ctx.admin
    .from("dataset_permissions")
    .upsert({
      dataset_id: datasetId,
      user_id: userId,
      permission,
      granted_by: ctx.user.id,
    });

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  await ctx.admin.from("audit_events").insert({
    organization_id: ctx.dataset.organization_id,
    actor_user_id: ctx.user.id,
    event_type: "permission.granted",
    resource_type: "dataset",
    resource_id: datasetId,
    event_metadata: { user_id: userId, permission },
  });

  return NextResponse.json({ granted: true });
}

/**
 * DELETE /api/datasets/[datasetId]/permissions
 * Revoke a permission from a user. Requires owner or admin.
 */
export async function DELETE(request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveAdminContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });

  const body = await request.json().catch(() => null);
  if (!body) return NextResponse.json({ error: "Invalid request body" }, { status: 400 });

  const { userId, permission } = body;
  if (!userId || !permission) {
    return NextResponse.json({ error: "userId and permission are required" }, { status: 400 });
  }

  const { error } = await ctx.admin
    .from("dataset_permissions")
    .delete()
    .eq("dataset_id", datasetId)
    .eq("user_id", userId)
    .eq("permission", permission);

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  await ctx.admin.from("audit_events").insert({
    organization_id: ctx.dataset.organization_id,
    actor_user_id: ctx.user.id,
    event_type: "permission.revoked",
    resource_type: "dataset",
    resource_id: datasetId,
    event_metadata: { user_id: userId, permission },
  });

  return NextResponse.json({ revoked: true });
}
