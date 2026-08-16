import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { sharedRateLimit } from "@/lib/shared-rate-limit";
import { grantCreatorDatasetAccess } from "@/lib/dataset-auth";
import { rowRestrictionsSchema } from "@/lib/domain";

const workerUrl = process.env.ANALYTICS_WORKER_URL;

export async function POST(request: Request) {
  const supabase = await createClient();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Sign in before uploading a dataset." }, { status: 401 });
  if (!(await sharedRateLimit(user.id, "upload", 5, 60))) return NextResponse.json({ error: "Upload rate limit unavailable or reached. Try again in a minute." }, { status: 429 });
  if (!workerUrl) return NextResponse.json({ error: "ANALYTICS_WORKER_URL is not configured." }, { status: 503 });

  const admin = createAdminClient();
  const form = await request.formData();
  const requestedOrganization = String(form.get("organizationId") || request.headers.get("x-organization-id") || "");
  let memberQuery = admin.from("organization_members").select("organization_id,role").eq("user_id", user.id);
  if (requestedOrganization) memberQuery = memberQuery.eq("organization_id", requestedOrganization);
  // Deterministic when no organization is supplied, so an upload does not
  // land in an arbitrary workspace for a multi-org user.
  const { data: member } = await memberQuery.order("created_at", { ascending: true }).limit(1).maybeSingle();
  if (!member) return NextResponse.json({ error: "You are not a member of the selected organization." }, { status: 403 });
  if (!["owner", "admin"].includes(member.role)) return NextResponse.json({ error: `Dataset uploads require owner or admin access. Your current role is ${member.role}.` }, { status: 403 });
  const file = form.get("file");
  if (!(file instanceof File) || !/\.(csv|parquet)$/i.test(file.name)) return NextResponse.json({ error: "Choose a CSV or Parquet file." }, { status: 400 });

  let publicBounds: Record<string, [number, number]>;
  let publicCategories: Record<string,string[]>;
  try {
    const value = JSON.parse(String(form.get("publicBounds") || "{}"));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    publicBounds = value;
    for (const bounds of Object.values(publicBounds)) if (!Array.isArray(bounds) || bounds.length !== 2 || !bounds.every(Number.isFinite) || bounds[0] >= bounds[1]) throw new Error();
  } catch { return NextResponse.json({ error: "Public bounds must map columns to finite [lower, upper] pairs." }, { status: 400 }); }
  try{const value=JSON.parse(String(form.get("publicCategories")||"{}"));if(!value||typeof value!=="object"||Array.isArray(value))throw new Error();publicCategories=value;for(const categories of Object.values(publicCategories))if(!Array.isArray(categories)||!categories.length||categories.length>100||categories.some(item=>typeof item!=="string"||!item.length||item.length>80)||new Set(categories).size!==categories.length)throw new Error();}catch{return NextResponse.json({error:"Public categories must map fields to 1-100 unique labels."},{status:400});}

  // Sensitive columns may still be measured (mean/sum/histogram) but are
  // never allowed as a group_by or filter column -- enforced later against
  // the ingested schema, and again at query time. Column existence is
  // checked below once the worker returns the real schema.
  let sensitiveColumns: string[];
  try {
    const value = JSON.parse(String(form.get("sensitiveColumns") || "[]"));
    if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item.length)) throw new Error();
    sensitiveColumns = value;
  } catch { return NextResponse.json({ error: "Sensitive columns must be a JSON array of column names." }, { status: 400 }); }

  // Row restrictions are owner-defined predicates ANDed into every query by
  // the worker before aggregation. Shape validated with the same schema
  // analyst filters use (rowRestrictionsSchema wraps filterSchema); column
  // existence is checked below against the ingested schema.
  let rowRestrictions: Array<{ column: string; operator: "equals" | "not_equals"; value: string }>;
  try {
    const value = JSON.parse(String(form.get("rowRestrictions") || "[]"));
    const parsed = rowRestrictionsSchema.safeParse(value);
    if (!parsed.success) throw new Error();
    rowRestrictions = parsed.data;
  } catch { return NextResponse.json({ error: "Row restrictions must be a JSON array of at most 10 {column, operator, value} entries, with operator equals or not_equals." }, { status: 400 }); }

  // Entity column is optional: null means the row itself is the privacy
  // unit, today's (unenforced) default. When supplied, it must name a real
  // column once ingestion returns the schema, checked below.
  const entityColumnRaw = String(form.get("entityColumn") || "").trim();
  const entityColumn = entityColumnRaw.length ? entityColumnRaw : null;
  const maxContributionsRaw = form.get("maxContributions");
  let maxContributions = 1;
  if (maxContributionsRaw != null && String(maxContributionsRaw).length) {
    const parsed = Number(maxContributionsRaw);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) return NextResponse.json({ error: "Max contributions must be an integer between 1 and 100." }, { status: 400 });
    maxContributions = parsed;
  }

  const { data: dataset, error: datasetError } = await admin.from("datasets").insert({ organization_id: member.organization_id, name: String(form.get("name") || file.name.replace(/\.(csv|parquet)$/i, "")), description: String(form.get("description") || "Uploaded protected dataset"), created_by: user.id, status: "processing" }).select("id").single();
  if (datasetError) return NextResponse.json({ error: datasetError.message }, { status: 500 });
  let storageKey: string | undefined;
  try {
    const workerForm = new FormData();
    workerForm.append("upload", file, file.name);
    const response = await fetch(`${workerUrl}/v1/ingest?dataset_id=${dataset.id}`, { method: "POST", headers: { "x-worker-token": process.env.ANALYTICS_WORKER_TOKEN || "" }, body: workerForm });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || result.error || "Dataset ingestion failed.");
    storageKey = result.storage_key;
    const schema = (result.schema || []).map((column: { name: string; data_type: string; can_group: boolean; can_measure: boolean }) => {
      const bounds = publicBounds[column.name];
      const measurable = column.can_measure && Array.isArray(bounds);
      return { ...column, dataset_id: dataset.id, can_measure: measurable, lower_bound: measurable ? bounds[0] : null, upper_bound: measurable ? bounds[1] : null, is_sensitive: sensitiveColumns.includes(column.name) };
    });
    const unknownBounds = Object.keys(publicBounds).filter(name => !schema.some((column: { name: string }) => column.name === name));
    if (unknownBounds.length) throw new Error(`Public bounds reference unknown columns: ${unknownBounds.join(", ")}`);
    const unknownCategories=Object.keys(publicCategories).filter(name=>!schema.some((column:{name:string;can_group:boolean})=>column.name===name&&column.can_group));if(unknownCategories.length)throw new Error(`Public categories reference unavailable grouping fields: ${unknownCategories.join(", ")}`);
    // Entity column must exist in the ingested schema; otherwise contribution
    // bounding would be silently unenforceable against it.
    if (entityColumn && !schema.some((column: { name: string }) => column.name === entityColumn)) throw new Error(`Entity column '${entityColumn}' is not present in the ingested schema.`);
    // Sensitive columns and row restrictions must reference real columns, or
    // the marking/restriction would be silently unenforceable.
    const unknownSensitive = sensitiveColumns.filter((name) => !schema.some((column: { name: string }) => column.name === name));
    if (unknownSensitive.length) throw new Error(`Sensitive columns reference unknown columns: ${unknownSensitive.join(", ")}`);
    const unknownRestrictions = rowRestrictions.filter((item) => !schema.some((column: { name: string }) => column.name === item.column));
    if (unknownRestrictions.length) throw new Error(`Row restrictions reference unknown columns: ${[...new Set(unknownRestrictions.map((item) => item.column))].join(", ")}`);
    // The privacy unit follows the entity column: set means the entity is the
    // unit, null keeps today's row-is-the-unit default.
    const privacyUnit = entityColumn ? entityColumn : "row";
    const { error: policyError } = await admin.from("privacy_policies").insert({ dataset_id: dataset.id, epsilon_total: 5, epsilon_used: 0, delta_total: 0.000001, min_group_size: 5, max_groups: 20, privacy_unit: privacyUnit, public_min_denominator: 10,public_categories:publicCategories, entity_column: entityColumn, max_contributions: maxContributions, row_restrictions: rowRestrictions });
    if (policyError) throw policyError;
    const { error: columnsError } = await admin.from("dataset_columns").insert(schema);
    if (columnsError) throw columnsError;
    const { error: finalizeError } = await admin.from("datasets").update({ row_count: result.rows, column_count: result.columns, storage_key: storageKey, file_format: "parquet+fernet", status: "protected" }).eq("id", dataset.id);
    if (finalizeError) throw finalizeError;
    await grantCreatorDatasetAccess(admin, dataset.id, user.id);
    // sensitive_columns is safe to record -- column names are already
    // visible to anyone with view_schema. row_restrictions_count is a count
    // only: the restriction's column/operator/value must never reach the
    // audit log, since a restriction may itself encode a sensitive predicate.
    const { error: auditError } = await admin.from("audit_events").insert({ organization_id: member.organization_id, actor_user_id: user.id, event_type: "dataset.uploaded", resource_type: "dataset", resource_id: dataset.id, event_metadata: { rows: result.rows, columns: result.columns, privacy_unit: privacyUnit, entity_column: entityColumn, max_contributions: maxContributions, sensitive_columns: sensitiveColumns, row_restrictions_count: rowRestrictions.length } });
    if (auditError) throw auditError;
    return NextResponse.json({ datasetId: dataset.id, rows: result.rows, columns: result.columns });
  } catch (error) {
    await fetch(`${workerUrl}/v1/storage/delete`, { method: "POST", headers: { "Content-Type": "application/json", "x-worker-token": process.env.ANALYTICS_WORKER_TOKEN || "" }, body: JSON.stringify({ dataset_id: dataset.id }) }).catch(() => undefined);
    await admin.from("datasets").delete().eq("id", dataset.id);
    const message = error instanceof Error ? error.message : "Dataset ingestion failed.";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
