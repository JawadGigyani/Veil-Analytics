import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { canViewOrgAudit } from "@/lib/dataset-auth";

export async function GET(request: Request) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const url = new URL(request.url);
  const organizationId = url.searchParams.get("organizationId");
  const eventType = url.searchParams.get("eventType");
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50"), 100);
  const offset = parseInt(url.searchParams.get("offset") || "0");

  if (!organizationId) {
    return NextResponse.json({ error: "organizationId is required" }, { status: 400 });
  }

  const admin = createAdminClient();

  // Verify membership
  const { data: membership } = await admin
    .from("organization_members")
    .select("role")
    .eq("organization_id", organizationId)
    .eq("user_id", user.id)
    .maybeSingle();

  if (!membership) {
    return NextResponse.json({ error: "Organization membership required" }, { status: 403 });
  }

  const canAudit = await canViewOrgAudit(admin, organizationId, user.id, membership.role);
  if (!canAudit) {
    return NextResponse.json({
      error: "Audit log access requires owner/admin role or view_audit_log permission.",
    }, { status: 403 });
  }

  // Build query
  let query = admin
    .from("audit_events")
    .select("id,event_type,resource_type,resource_id,actor_user_id,event_metadata,created_at")
    .eq("organization_id", organizationId)
    .order("created_at", { ascending: false })
    .range(offset, offset + limit - 1);

  if (eventType) {
    query = query.eq("event_type", eventType);
  }

  const { data: events, error } = await query;

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  // Resolve actor emails
  const actorIds = [...new Set((events || []).map(e => e.actor_user_id).filter(Boolean))];
  const actorMap: Record<string, string> = {};
  for (const actorId of actorIds) {
    const { data } = await admin.auth.admin.getUserById(actorId);
    if (data.user?.email) {
      actorMap[actorId] = data.user.email;
    }
  }

  const enriched = (events || []).map(event => ({
    ...event,
    actor_email: event.actor_user_id ? actorMap[event.actor_user_id] || null : null,
  }));

  return NextResponse.json({
    events: enriched,
    limit,
    offset,
    hasMore: (events || []).length === limit,
  });
}
