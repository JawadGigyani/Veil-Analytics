import type { SupabaseClient } from "@supabase/supabase-js";

export type OrgRole = "owner" | "admin" | "analyst" | string;
export type DatasetPermission =
  | "view_schema"
  | "run_queries"
  | "manage_dataset"
  | "view_audit_log";

type AdminClient = SupabaseClient;

/** Owners and admins have implicit full dataset access. */
export function hasImplicitDatasetAccess(role: OrgRole): boolean {
  return role === "owner" || role === "admin";
}

/**
 * Pure decision helper for unit tests and route checks.
 * Owners/admins always pass. Analysts need an explicit permission grant.
 */
export function canAccessDataset(
  role: OrgRole,
  grantedPermissions: readonly string[],
  required: DatasetPermission,
): boolean {
  if (hasImplicitDatasetAccess(role)) return true;
  return grantedPermissions.includes(required);
}

export async function fetchDatasetPermissions(
  admin: AdminClient,
  datasetId: string,
  userId: string,
): Promise<string[]> {
  const { data, error } = await admin
    .from("dataset_permissions")
    .select("permission")
    .eq("dataset_id", datasetId)
    .eq("user_id", userId);
  if (error) throw new Error(error.message);
  return (data || []).map((row) => row.permission);
}

export async function userHasDatasetPermission(
  admin: AdminClient,
  datasetId: string,
  userId: string,
  role: OrgRole,
  required: DatasetPermission,
): Promise<boolean> {
  if (hasImplicitDatasetAccess(role)) return true;
  const granted = await fetchDatasetPermissions(admin, datasetId, userId);
  return canAccessDataset(role, granted, required);
}

/** Grant one or more permissions; ignores duplicate primary-key conflicts. */
export async function grantDatasetPermissions(
  admin: AdminClient,
  datasetId: string,
  userId: string,
  permissions: DatasetPermission[],
  grantedBy?: string,
): Promise<void> {
  if (!permissions.length) return;
  const rows = permissions.map((permission) => ({
    dataset_id: datasetId,
    user_id: userId,
    permission,
    granted_by: grantedBy ?? userId,
  }));
  const { error } = await admin
    .from("dataset_permissions")
    .upsert(rows, { onConflict: "dataset_id,user_id,permission", ignoreDuplicates: true });
  if (error) throw new Error(error.message);
}

/** Owner/creator grants used by bootstrap and upload. */
export async function grantCreatorDatasetAccess(
  admin: AdminClient,
  datasetId: string,
  userId: string,
): Promise<void> {
  await grantDatasetPermissions(admin, datasetId, userId, [
    "manage_dataset",
    "run_queries",
    "view_schema",
    "view_audit_log",
  ], userId);
}

/** When an analyst joins an org, grant query access on active protected datasets. */
export async function grantAnalystRunQueriesOnOrgDatasets(
  admin: AdminClient,
  organizationId: string,
  userId: string,
  grantedBy: string,
): Promise<void> {
  const { data: datasets, error } = await admin
    .from("datasets")
    .select("id")
    .eq("organization_id", organizationId)
    .eq("status", "protected")
    .is("revoked_at", null);
  if (error) throw new Error(error.message);
  for (const dataset of datasets || []) {
    await grantDatasetPermissions(admin, dataset.id, userId, ["run_queries", "view_schema"], grantedBy);
  }
}

export async function filterDatasetsForRole(
  admin: AdminClient,
  datasets: { id: string }[],
  userId: string,
  role: OrgRole,
): Promise<string[]> {
  if (hasImplicitDatasetAccess(role)) return datasets.map((d) => d.id);
  if (!datasets.length) return [];
  const ids = datasets.map((d) => d.id);
  const { data, error } = await admin
    .from("dataset_permissions")
    .select("dataset_id")
    .eq("user_id", userId)
    .eq("permission", "run_queries")
    .in("dataset_id", ids);
  if (error) throw new Error(error.message);
  return [...new Set((data || []).map((row) => row.dataset_id))];
}

/** Audit access: owner/admin, or explicit view_audit_log on any org dataset. */
export async function canViewOrgAudit(
  admin: AdminClient,
  organizationId: string,
  userId: string,
  role: OrgRole,
): Promise<boolean> {
  if (hasImplicitDatasetAccess(role)) return true;
  const { data: datasets, error: datasetError } = await admin
    .from("datasets")
    .select("id")
    .eq("organization_id", organizationId);
  if (datasetError) throw new Error(datasetError.message);
  const ids = (datasets || []).map((d) => d.id);
  if (!ids.length) return false;
  const { data, error } = await admin
    .from("dataset_permissions")
    .select("dataset_id")
    .eq("user_id", userId)
    .eq("permission", "view_audit_log")
    .in("dataset_id", ids)
    .limit(1);
  if (error) throw new Error(error.message);
  return (data || []).length > 0;
}
