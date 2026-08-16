import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { hasImplicitDatasetAccess, userHasDatasetPermission } from "@/lib/dataset-auth";

interface RouteContext {
  params: Promise<{ datasetId: string }>;
}

async function resolveContext(datasetId: string) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const admin = createAdminClient();
  const { data: dataset } = await admin
    .from("datasets")
    .select("id,organization_id,name,description,row_count,column_count,status,file_format,storage_key,created_at,revoked_at")
    .eq("id", datasetId)
    .maybeSingle();

  if (!dataset) return null;

  const { data: membership } = await admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", dataset.organization_id)
    .eq("user_id", user.id)
    .maybeSingle();

  if (!membership) return null;

  return { user, admin, dataset, role: membership.role };
}

/**
 * GET /api/datasets/[datasetId]
 * Returns dataset details including schema, policy, and permissions.
 */
export async function GET(_request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Dataset not found or access denied" }, { status: 404 });

  const canView = await userHasDatasetPermission(
    ctx.admin, datasetId, ctx.user.id, ctx.role, "view_schema",
  );
  if (!canView) {
    return NextResponse.json({ error: "Dataset not found or access denied" }, { status: 404 });
  }

  const [{ data: policy }, { data: columns }, { data: permissions }] = await Promise.all([
    ctx.admin.from("privacy_policies").select("*").eq("dataset_id", datasetId).single(),
    ctx.admin.from("dataset_columns").select("*").eq("dataset_id", datasetId).order("name"),
    hasImplicitDatasetAccess(ctx.role)
      ? ctx.admin.from("dataset_permissions").select("user_id,permission,created_at").eq("dataset_id", datasetId)
      : Promise.resolve({ data: [] as { user_id: string; permission: string; created_at: string }[] }),
  ]);

  return NextResponse.json({
    dataset: {
      ...ctx.dataset,
      // Do not expose storage keys to non-admins.
      storage_key: hasImplicitDatasetAccess(ctx.role) ? ctx.dataset.storage_key : undefined,
    },
    policy,
    columns: columns || [],
    permissions: permissions || [],
    role: ctx.role,
  });
}

/**
 * PATCH /api/datasets/[datasetId]
 * Revoke (archive) a dataset. Sets revoked_at and status = 'archived'.
 */
export async function PATCH(request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Dataset not found or access denied" }, { status: 404 });
  if (!["owner", "admin"].includes(ctx.role)) {
    return NextResponse.json({ error: "Owner or admin access required to revoke a dataset" }, { status: 403 });
  }

  const body = await request.json().catch(() => ({}));
  const action = body.action;

  if (action === "revoke") {
    if (ctx.dataset.revoked_at) {
      return NextResponse.json({ error: "Dataset is already revoked" }, { status: 409 });
    }

    const { error } = await ctx.admin
      .from("datasets")
      .update({ status: "archived", revoked_at: new Date().toISOString() })
      .eq("id", datasetId);

    if (error) return NextResponse.json({ error: error.message }, { status: 500 });

    await ctx.admin.from("audit_events").insert({
      organization_id: ctx.dataset.organization_id,
      actor_user_id: ctx.user.id,
      event_type: "dataset.revoked",
      resource_type: "dataset",
      resource_id: datasetId,
      event_metadata: { name: ctx.dataset.name },
    });

    return NextResponse.json({ revoked: true });
  }

  return NextResponse.json({ error: "Unsupported action" }, { status: 400 });
}

/**
 * DELETE /api/datasets/[datasetId]
 * Permanently delete a dataset and its encrypted storage object.
 */
export async function DELETE(_request: Request, context: RouteContext) {
  const { datasetId } = await context.params;
  const ctx = await resolveContext(datasetId);
  if (!ctx) return NextResponse.json({ error: "Dataset not found or access denied" }, { status: 404 });
  if (ctx.role !== "owner") {
    return NextResponse.json({ error: "Only the organization owner can delete a dataset" }, { status: 403 });
  }

  // Delete encrypted storage if worker is available (resolved server-side by dataset_id)
  if (process.env.ANALYTICS_WORKER_URL) {
    await fetch(`${process.env.ANALYTICS_WORKER_URL}/v1/storage/delete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-worker-token": process.env.ANALYTICS_WORKER_TOKEN || "",
      },
      body: JSON.stringify({ dataset_id: datasetId }),
    }).catch(() => undefined);
  }

  const { error } = await ctx.admin.from("datasets").delete().eq("id", datasetId);
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  await ctx.admin.from("audit_events").insert({
    organization_id: ctx.dataset.organization_id,
    actor_user_id: ctx.user.id,
    event_type: "dataset.deleted",
    resource_type: "dataset",
    resource_id: datasetId,
    event_metadata: { name: ctx.dataset.name },
  });

  return NextResponse.json({ deleted: true });
}
