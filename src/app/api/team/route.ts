import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { grantAnalystRunQueriesOnOrgDatasets } from "@/lib/dataset-auth";

async function ownerContext(organizationId: string) {
  const session = await createClient();
  const { data: { user } } = await session.auth.getUser();
  if (!user) return null;
  const admin = createAdminClient();
  const { data: membership } = await admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", organizationId)
    .eq("user_id", user.id)
    .maybeSingle();
  return membership && ["owner", "admin"].includes(membership.role)
    ? { user, admin }
    : null;
}

export async function GET(request: Request) {
  const organizationId = new URL(request.url).searchParams.get("organizationId");
  if (!organizationId) return NextResponse.json({ error: "organizationId required" }, { status: 400 });
  const context = await ownerContext(organizationId);
  if (!context) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });
  const { data: members } = await context.admin
    .from("organization_members")
    .select("user_id,role,created_at")
    .eq("organization_id", organizationId);
  const users = await Promise.all(
    (members || []).map(async (member) => {
      const { data } = await context.admin.auth.admin.getUserById(member.user_id);
      return { ...member, email: data.user?.email };
    }),
  );
  return NextResponse.json({ members: users });
}

export async function POST(request: Request) {
  const { organizationId, email, role = "analyst" } = await request.json();
  const context = await ownerContext(organizationId);
  if (!context) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });
  if (!["admin", "analyst"].includes(role)) return NextResponse.json({ error: "Invalid role" }, { status: 400 });
  const { data, error } = await context.admin.auth.admin.inviteUserByEmail(email, {
    data: { invited_organization_id: organizationId, invited_role: role },
  });
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  if (data.user) {
    await context.admin
      .from("organization_members")
      .upsert({ organization_id: organizationId, user_id: data.user.id, role });
    if (role === "analyst") {
      await grantAnalystRunQueriesOnOrgDatasets(
        context.admin, organizationId, data.user.id, context.user.id,
      ).catch(() => undefined);
    }
  }
  return NextResponse.json({ invited: true });
}

export async function PATCH(request: Request) {
  const { organizationId, userId, role } = await request.json();
  const context = await ownerContext(organizationId);
  if (!context) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });
  if (!["admin", "analyst"].includes(role)) return NextResponse.json({ error: "Invalid role" }, { status: 400 });
  const { error } = await context.admin
    .from("organization_members")
    .update({ role })
    .eq("organization_id", organizationId)
    .eq("user_id", userId);
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  if (role === "analyst") {
    await grantAnalystRunQueriesOnOrgDatasets(
      context.admin, organizationId, userId, context.user.id,
    ).catch(() => undefined);
  }
  return NextResponse.json({ updated: true });
}

export async function DELETE(request: Request) {
  const { organizationId, userId } = await request.json();
  const context = await ownerContext(organizationId);
  if (!context) return NextResponse.json({ error: "Owner or admin access required" }, { status: 403 });
  const { data: target } = await context.admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", organizationId)
    .eq("user_id", userId)
    .maybeSingle();
  if (!target) return NextResponse.json({ error: "Member not found" }, { status: 404 });
  if (target.role === "owner") {
    return NextResponse.json({ error: "The organization owner cannot be revoked." }, { status: 409 });
  }
  const { error } = await context.admin
    .from("organization_members")
    .delete()
    .eq("organization_id", organizationId)
    .eq("user_id", userId);
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });

  // Drop dataset-level grants for the revoked member within this organization.
  const { data: datasets } = await context.admin
    .from("datasets")
    .select("id")
    .eq("organization_id", organizationId);
  const datasetIds = (datasets || []).map((d) => d.id);
  if (datasetIds.length) {
    await context.admin
      .from("dataset_permissions")
      .delete()
      .eq("user_id", userId)
      .in("dataset_id", datasetIds);
  }

  await context.admin.from("audit_events").insert({
    organization_id: organizationId,
    actor_user_id: context.user.id,
    event_type: "member.revoked",
    resource_type: "organization_member",
    resource_id: userId,
    event_metadata: {},
  });
  return NextResponse.json({ revoked: true });
}
