import { createHash, randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { querySchema, usesSensitiveColumnForGroupingOrFiltering } from "@/lib/domain";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { sharedRateLimit } from "@/lib/shared-rate-limit";
import { userHasDatasetPermission } from "@/lib/dataset-auth";
import { auditableQueryMetadata } from "@/lib/audit";

function normalizeQueryHash(spec: Record<string, unknown>): string {
  const canonical = JSON.stringify(spec, Object.keys(spec).sort());
  return createHash("sha256").update(canonical).digest("hex").slice(0, 16);
}

export async function POST(request: Request) {
  let body: unknown;
  try { body = await request.json(); } catch { return NextResponse.json({ error: "Invalid JSON request." }, { status: 400 }); }
  const parsed = querySchema.safeParse(body);
  if (!parsed.success) {
    // querySchema's superRefine already says exactly what is wrong ("Laplace
    // releases must not spend delta", "A grouping column is required", ...).
    // Collapsing all of them into one sentence about epsilon sent analysts to
    // adjust a value that was never the problem. Only the schema's own
    // messages are surfaced -- they describe the request shape the caller
    // sent, never dataset contents.
    const issue = parsed.error.issues[0];
    return NextResponse.json({
      error: issue?.message ?? "Choose a supported operation and a privacy cost between 0.1 and 2.",
    }, { status: 400 });
  }
  const spec = parsed.data;
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) return NextResponse.json({ error: "Supabase is required for dataset-backed releases." }, { status: 503 });

  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Sign in before releasing a protected answer." }, { status: 401 });
  if (!(await sharedRateLimit(user.id, "query", 20, 60))) return NextResponse.json({ error: "Query rate limit unavailable or reached. Retry in a minute." }, { status: 429 });

  const admin = createAdminClient();
  const activeOrganization = request.headers.get("x-organization-id");
  let membershipQuery = admin.from("organization_members").select("organization_id,role").eq("user_id", user.id);
  if (activeOrganization) membershipQuery = membershipQuery.eq("organization_id", activeOrganization);
  // Ordered so a user in several organizations always resolves to the same
  // one when no x-organization-id header is sent. Without it the choice is
  // whatever the planner returns, and budget is spent against an arbitrary
  // dataset. Always a workspace the user belongs to, so not an access issue.
  const { data: membership } = await membershipQuery.order("created_at", { ascending: true }).limit(1).maybeSingle();
  if (!membership) return NextResponse.json({ error: "Your workspace is not initialized yet." }, { status: 409 });

  let datasetQuery = admin.from("datasets").select("id,storage_key,file_format,revoked_at").eq("organization_id", membership.organization_id).eq("status", "protected");
  if (spec.datasetId) datasetQuery = datasetQuery.eq("id", spec.datasetId);
  const { data: dataset } = await datasetQuery.order("created_at", { ascending: true }).limit(1).maybeSingle();
  if (!dataset) return NextResponse.json({ error: "No protected dataset is configured." }, { status: 409 });
  if (dataset.revoked_at) return NextResponse.json({ error: "This dataset has been revoked and no longer accepts queries." }, { status: 410 });

  const allowed = await userHasDatasetPermission(admin, dataset.id, user.id, membership.role, "run_queries");
  if (!allowed) {
    return NextResponse.json({ error: "You do not have permission to run queries on this dataset." }, { status: 403 });
  }

  const { data: policy } = await admin.from("privacy_policies").select("min_group_size,max_groups,public_min_denominator,public_categories,allowed_query_types,epsilon_total,epsilon_used,delta_total,delta_used,entity_column,max_contributions,row_restrictions").eq("dataset_id", dataset.id).single();
  if (!policy) return NextResponse.json({ error: "The dataset privacy policy is unavailable." }, { status: 409 });
  if (!policy.allowed_query_types?.includes(spec.type)) return NextResponse.json({ error: "That operation is not enabled for this dataset." }, { status: 403 });
  const { data: columns } = await admin.from("dataset_columns").select("name,data_type,can_group,can_measure,lower_bound,upper_bound,is_sensitive").eq("dataset_id", dataset.id);
  const columnMap = new Map((columns || []).map(column => [column.name, column]));
  const requested = [spec.groupBy, spec.valueColumn, ...spec.filters.map(filter => filter.column)].filter(Boolean) as string[];
  if (requested.some(column => !columnMap.has(column))) return NextResponse.json({ error: "The query references a column that is not available." }, { status: 400 });
  // Sensitive columns may still be measured, but a group label or filter
  // predicate on one reveals the attribute directly no matter how much noise
  // is added to the aggregate released alongside it -- see migration-008.
  // The column name itself is safe to name back (already visible via
  // view_schema); only filter *values* stay out of any response.
  const sensitiveColumnNames = (columns || []).filter(column => column.is_sensitive).map(column => column.name);
  const sensitiveHit = usesSensitiveColumnForGroupingOrFiltering(spec, sensitiveColumnNames);
  if (sensitiveHit) return NextResponse.json({ error: `'${sensitiveHit}' is marked sensitive and cannot be used for grouping or filtering.` }, { status: 400 });
  if (spec.groupBy && !columnMap.get(spec.groupBy)?.can_group) return NextResponse.json({ error: "That field cannot be used for grouping." }, { status: 400 });
  const groupCategories=spec.groupBy?(policy.public_categories as Record<string,string[]> | null)?.[spec.groupBy]:undefined;
  if(spec.groupBy&&(!Array.isArray(groupCategories)||!groupCategories.length))return NextResponse.json({error:"This field needs a public category domain before grouped releases are allowed."},{status:409});
  if (spec.valueColumn && !columnMap.get(spec.valueColumn)?.can_measure) return NextResponse.json({ error: "That field cannot be measured." }, { status: 400 });
  const valueColumn = spec.valueColumn ? columnMap.get(spec.valueColumn) : undefined;
  if (spec.valueColumn && (valueColumn?.lower_bound == null || valueColumn?.upper_bound == null)) return NextResponse.json({ error: "This numeric field needs public clipping bounds before it can be queried." }, { status: 409 });

  // Charge before data access. This is intentionally conservative: failed execution is not refunded.
  const queryId = randomUUID();
  const queryHash = normalizeQueryHash(spec as unknown as Record<string, unknown>);
  const { error: reserveError } = await admin.rpc("reserve_privacy_budget", {
    target_query: queryId, target_dataset: dataset.id, target_actor: user.id,
    target_epsilon: spec.epsilon, target_spec: spec, target_operation: spec.type,
    target_delta: spec.delta,
  });
  if (reserveError) return NextResponse.json({ error: reserveError.message }, { status: 409 });

  // Store normalized hash for deduplication tracking
  await admin.from("queries").update({ normalized_query_hash: queryHash }).eq("id", queryId);

  // The reservation is the moment budget is spent, so it is audited whether or
  // not execution goes on to succeed. Audit writes never block a release: a
  // failure here must not strand a query that has already been charged.
  const auditMetadata = auditableQueryMetadata(spec);
  const writeAudit = (eventType: string, extra: Record<string, unknown> = {}) =>
    admin.from("audit_events").insert({
      organization_id: membership.organization_id,
      actor_user_id: user.id,
      event_type: eventType,
      resource_type: "query",
      resource_id: queryId,
      event_metadata: { ...auditMetadata, ...extra, dataset_id: dataset.id, query_hash: queryHash },
    }).then(() => undefined, () => undefined);

  // row_restrictions_count is a count only -- the restriction's column,
  // operator, and value must never reach the audit log, since a restriction
  // may itself encode a sensitive predicate (see migration-008).
  const rowRestrictions = Array.isArray(policy.row_restrictions) ? policy.row_restrictions : [];
  await writeAudit("query.reserved", { mechanism: spec.mechanism, delta: spec.delta, row_restrictions_count: rowRestrictions.length });

  try {
    let result: Record<string, unknown>;
    if (dataset.storage_key) {
      if (!process.env.ANALYTICS_WORKER_URL) throw new Error("The Python analytics worker is not configured.");
      const response = await fetch(`${process.env.ANALYTICS_WORKER_URL}/v1/query`, {
        method: "POST", headers: { "Content-Type": "application/json", "x-worker-token": process.env.ANALYTICS_WORKER_TOKEN || "" },
        body: JSON.stringify({
          dataset_id: dataset.id,
          query_type: spec.type,
          epsilon: spec.epsilon,
          group_by: spec.groupBy,
          group_categories: groupCategories || [],
          value_column: spec.valueColumn,
          lower: valueColumn?.lower_bound,
          upper: valueColumn?.upper_bound,
          bins: spec.bins,
          top_k: spec.topK,
          min_group_size: policy.min_group_size || 5,
          max_groups: policy.max_groups || 20,
          minimum_denominator: policy.public_min_denominator || 10,
          filters: spec.filters,
          mechanism: spec.mechanism,
          delta: spec.delta,
          // Contribution bounding: null entity_column with max_contributions 1
          // reproduces today's one-row-per-person assumption exactly. When an
          // entity_column is set, the worker bounds each entity's row count
          // before aggregating so sensitivity reflects the real privacy unit.
          entity_column: policy.entity_column ?? null,
          max_contributions: policy.max_contributions ?? 1,
          // Owner-defined predicates the worker ANDs into every query before
          // aggregation. Never logged, never echoed back -- see
          // migration-008 and src/lib/domain.ts. sensitive_columns lets the
          // worker re-validate the groupBy/filter rejection above
          // independently of this route.
          row_restrictions: rowRestrictions,
          sensitive_columns: sensitiveColumnNames,
          // Forwarded so the worker re-validates the query against the
          // dataset schema and policy instead of trusting these checks.
          // epsilon_used is the pre-reservation figure read above, so the
          // worker's budget check mirrors the one the database enforced.
          columns: (columns || []).map(column => ({
            name: column.name,
            data_type: column.data_type,
            can_group: column.can_group,
            can_measure: column.can_measure,
            lower_bound: column.lower_bound,
            upper_bound: column.upper_bound,
            is_sensitive: column.is_sensitive,
          })),
          epsilon_total: Number(policy.epsilon_total),
          epsilon_used: Number(policy.epsilon_used),
          // Delta budget gets the same second gate epsilon does. The database
          // is the enforcement point -- it checks and spends delta under the
          // policy row lock -- but the worker re-checks against the figures
          // read before reservation, so a bug in this route cannot turn a
          // delta overspend into a release. Omitting these would have left
          // delta with strictly weaker checking than epsilon.
          delta_total: Number(policy.delta_total),
          delta_used: Number(policy.delta_used),
          allowed_query_types: policy.allowed_query_types,
        }),
      });
      const workerResult = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(workerResult.detail || "Worker query failed.");
      result = { ...workerResult, mechanism: spec.mechanism === "gaussian" ? "Gaussian" : "Laplace", note: "Executed by the server-only Python DuckDB worker against encrypted Parquet." };
    } else {
      throw new Error("Legacy raw-record execution is disabled; re-ingest this dataset into encrypted Parquet.");
    }
    const remaining = Number(policy.epsilon_total) - Number(policy.epsilon_used) - spec.epsilon;
    const remainingDelta = Number(policy.delta_total) - Number(policy.delta_used) - spec.delta;
    result.remaining_epsilon = remaining;
    result.remaining_delta = remainingDelta;
    // An owner restriction silently removes rows before aggregation, so an
    // analyst reading "196" over a 300-row dataset has no way to know the
    // figure covers a subset unless it is stated. The COUNT only: the
    // restriction's column, operator, and value stay server-side, since a
    // predicate may itself encode a sensitive attribute (see migration-008).
    // src/components/result-panel.tsx renders this; nothing was producing it.
    result.row_restrictions_applied = rowRestrictions.length;
    const { error } = await admin.rpc("finalize_privacy_query", { target_query: queryId, target_status: "completed", target_result: result });
    if (error) throw new Error(`Could not finalize privacy release: ${error.message}`);
    await writeAudit("query.released", { remaining_epsilon: remaining, remaining_delta: remainingDelta });
    return NextResponse.json({ queryId, result, remaining_epsilon: remaining, remaining_delta: remainingDelta });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Query execution failed.";
    await admin.rpc("finalize_privacy_query", { target_query: queryId, target_status: "failed", target_error: message });
    // Budget stays consumed on failure, so the audit trail has to show it.
    await writeAudit("query.failed", { budget_consumed: spec.epsilon, delta_consumed: spec.delta });
    return NextResponse.json({ error: message, queryId, budget_consumed: spec.epsilon, delta_consumed: spec.delta }, { status: 502 });
  }
}
