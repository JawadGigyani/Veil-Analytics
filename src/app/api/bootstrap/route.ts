/* global Blob */
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { grantCreatorDatasetAccess } from "@/lib/dataset-auth";

const demoColumns = [
  { name: "age", data_type: "number", can_group: false, can_measure: true, lower_bound: 18, upper_bound: 90 },
  { name: "region", data_type: "category", can_group: true, can_measure: false },
  { name: "care_program", data_type: "category", can_group: true, can_measure: false },
  { name: "outcome", data_type: "category", can_group: true, can_measure: false },
  { name: "insurance_type", data_type: "category", can_group: true, can_measure: false },
];

export async function POST() {
  const sessionClient = await createClient();
  const { data: { user } } = await sessionClient.auth.getUser();
  if (!user) return NextResponse.json({ error: "Sign in to create your workspace." }, { status: 401 });

  const admin = createAdminClient();
  const { data: memberships, error: membershipError } = await admin.from("organization_members").select("organization_id,role,created_at").eq("user_id", user.id).order("created_at", { ascending: true });
  if (membershipError) return NextResponse.json({ error: membershipError.message }, { status: 500 });
  const membership = memberships?.find(item => item.role === "owner") || memberships?.find(item => item.role === "admin") || memberships?.[0];
  if (membership) {
    const { data: existing } = await admin.from("datasets").select("id,storage_key").eq("organization_id", membership.organization_id).order("created_at", { ascending: true }).limit(1).maybeSingle();
    if (existing) {
      return repairWorkspace(
        admin, user.id, membership.role, membership.organization_id, existing.id, existing.storage_key,
      );
    }
    return createWorkspace(admin, user.id, membership.organization_id, user.email, false);
  }

  // Two callers POST this route on a normal sign-in (auth-screen.tsx, then
  // page.tsx after its reload), and provisioning seeds 1,200 records through
  // the worker before the membership row commits -- so the second request
  // observes "no membership" while the first is still running. The unique
  // index from migration-009 is what actually resolves the race: the losing
  // insert fails instead of provisioning a duplicate workspace.
  const { data: organization, error: orgError } = await admin
    .from("organizations")
    .insert({ name: `${user.email?.split("@")[0] ?? "My"} privacy workspace`, created_for_user: user.id })
    .select("id")
    .single();

  if (orgError) {
    // 23505 = the concurrent caller won. 42703 = migration-009 has not been
    // applied, so there is no column to race on; fall back to the previous
    // unguarded insert rather than blocking sign-in on a pending migration.
    if (orgError.code === "23505") return adoptWorkspaceCreatedByConcurrentCaller(admin, user.id, user.email);
    if (orgError.code !== "42703") return NextResponse.json({ error: orgError.message }, { status: 500 });

    const { data: legacy, error: legacyError } = await admin.from("organizations").insert({ name: `${user.email?.split("@")[0] ?? "My"} privacy workspace` }).select("id").single();
    if (legacyError) return NextResponse.json({ error: legacyError.message }, { status: 500 });
    return createWorkspace(admin, user.id, legacy.id, user.email, true);
  }

  return createWorkspace(admin, user.id, organization.id, user.email, true);
}

/**
 * Recovers the organization the winning concurrent bootstrap created. That
 * request may still be mid-flight, so the membership row this caller needs is
 * not guaranteed to exist yet; returning the organization id is enough for the
 * client, which reloads the workspace immediately afterwards.
 */
async function adoptWorkspaceCreatedByConcurrentCaller(
  admin: ReturnType<typeof createAdminClient>,
  userId: string,
  email: string | undefined,
) {
  const { data: organization } = await admin
    .from("organizations")
    .select("id")
    .eq("created_for_user", userId)
    .maybeSingle();
  if (!organization) {
    return NextResponse.json({ error: "Your workspace is still being created. Reload in a moment." }, { status: 409 });
  }

  const { data: dataset } = await admin
    .from("datasets")
    .select("id,storage_key")
    .eq("organization_id", organization.id)
    .order("created_at", { ascending: true })
    .limit(1)
    .maybeSingle();
  if (!dataset) {
    return NextResponse.json({ error: "Your workspace is still being created. Reload in a moment." }, { status: 409 });
  }

  await admin.from("profiles").upsert({ id: userId, display_name: email?.split("@")[0] || "Data owner" });
  return NextResponse.json({ ready: true, organizationId: organization.id, datasetId: dataset.id });
}

async function repairWorkspace(
  admin: ReturnType<typeof createAdminClient>,
  userId: string,
  role: string,
  organizationId: string,
  datasetId: string,
  storageKey: string | null,
) {
  const publicCategories={region:["north","south","east","west"],care_program:["access","recovery","prevention"],outcome:["improved","stable","follow_up"],insurance_type:["public","private","uninsured"]};
  // Grouped releases are refused outright unless the policy carries a public
  // category domain for the grouping column, so repairing it is the whole
  // point of this path. The previous implementation filtered with
  // `.eq("public_categories", {})`, which can never match SQL NULL -- a
  // policy row written before category domains existed kept NULL forever
  // while bootstrap went on reporting `ready: true`, leaving every grouped
  // release permanently refused on a workspace that looked healthy.
  const { data: existingPolicy, error: policyReadError } = await admin
    .from("privacy_policies")
    .select("dataset_id,public_categories")
    .eq("dataset_id", datasetId)
    .maybeSingle();
  if (policyReadError) return NextResponse.json({ error: policyReadError.message }, { status: 500 });

  let policyError: { message: string } | null = null;
  if (!existingPolicy) {
    ({ error: policyError } = await admin.from("privacy_policies").insert({ dataset_id: datasetId, epsilon_total: 5, epsilon_used: 0, delta_total: 0.000001, min_group_size: 5, public_categories: publicCategories }));
  } else {
    const current = existingPolicy.public_categories as Record<string, string[]> | null;
    if (!current || !Object.keys(current).length) {
      ({ error: policyError } = await admin.from("privacy_policies").update({ public_categories: publicCategories }).eq("dataset_id", datasetId));
    }
  }
  const { count: recordCount, error: recordCountError } = await admin.from("synthetic_health_records").select("id", { count: "exact", head: true }).eq("dataset_id", datasetId);
  if (policyError || recordCountError) return NextResponse.json({ error: policyError?.message || recordCountError?.message }, { status: 500 });
  const { data: columns } = await admin.from("dataset_columns").select("name").eq("dataset_id", datasetId);
  if (!columns?.length) {
    const { error } = await admin.from("dataset_columns").insert(demoColumns.map(column => ({ ...column, dataset_id: datasetId })));
    if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  }
  if (!storageKey) {
    const result = await seedRecords(datasetId);
    if (result.error) return NextResponse.json({ error: result.error }, { status: 500 });
    const {error}=await admin.from("datasets").update({ row_count: result.count, column_count: demoColumns.length, storage_key:result.storageKey, file_format:"parquet+fernet", status: "protected" }).eq("id", datasetId);
    if(error)return NextResponse.json({error:error.message},{status:500});
  }
  if(recordCount)await admin.from("synthetic_health_records").delete().eq("dataset_id",datasetId);
  // Only owners/admins get repaired creator grants; analysts keep invite-time grants.
  if (role === "owner" || role === "admin") {
    await grantCreatorDatasetAccess(admin, datasetId, userId).catch(() => undefined);
  }
  return NextResponse.json({ ready: true, organizationId, datasetId });
}

async function createWorkspace(admin: ReturnType<typeof createAdminClient>, userId: string, organizationId: string, email: string | undefined, createdOrganization: boolean) {

  const { error: profileError } = await admin.from("profiles").upsert({ id: userId, display_name: email?.split("@")[0] || "Data owner" });
  if (createdOrganization) {
    const { error: memberError } = await admin.from("organization_members").insert({ organization_id: organizationId, user_id: userId, role: "owner" });
    if (profileError || memberError) return NextResponse.json({ error: profileError?.message || memberError?.message }, { status: 500 });
  }
  const { data: dataset, error: datasetError } = await admin.from("datasets").insert({
    organization_id: organizationId,
    name: "Community health outcomes",
    description: "Synthetic records for a regional care-access program",
    row_count: 12480,
    column_count: 18,
    created_by: userId,
  }).select("id").single();
  if (datasetError) return NextResponse.json({ error: datasetError.message }, { status: 500 });

  const { error: policyError } = await admin.from("privacy_policies").insert({ dataset_id: dataset.id, epsilon_total: 5, epsilon_used: 0, delta_total: 0.000001, min_group_size: 5,public_categories:{region:["north","south","east","west"],care_program:["access","recovery","prevention"],outcome:["improved","stable","follow_up"],insurance_type:["public","private","uninsured"]} });
  const { error: columnError } = await admin.from("dataset_columns").insert(demoColumns.map(column => ({ ...column, dataset_id: dataset.id })));
  if (policyError || columnError) return NextResponse.json({ error: policyError?.message || columnError?.message }, { status: 500 });
  const result = await seedRecords(dataset.id);
  if (result.error) return NextResponse.json({ error: result.error }, { status: 500 });
  const {error:updateError}=await admin.from("datasets").update({ row_count: result.count, column_count: demoColumns.length, storage_key:result.storageKey, file_format:"parquet+fernet", status: "protected" }).eq("id", dataset.id);
  if(updateError)return NextResponse.json({error:updateError.message},{status:500});
  await grantCreatorDatasetAccess(admin, dataset.id, userId).catch(() => undefined);
  return NextResponse.json({ ready: true, organizationId, datasetId: dataset.id });
}

async function seedRecords(datasetId: string) {
  const regions=["north","south","east","west"], programs=["access","recovery","prevention"], outcomes=["improved","stable","follow_up"], insurance=["public","private","uninsured"];
  const records=Array.from({length:1200},(_,index)=>({dataset_id:datasetId,age:18+(index*17)%73,region:regions[index%4],care_program:programs[index%3],outcome:outcomes[(index*2)%3],insurance_type:insurance[index%3],length_of_stay:Number(((index%12)+1)*1.5)}));
  if(!process.env.ANALYTICS_WORKER_URL)return {error:"ANALYTICS_WORKER_URL is required to create the encrypted demo dataset.",count:0,storageKey:""};
  const csv=["age,region,care_program,outcome,insurance_type,length_of_stay",...records.map(record=>`${record.age},${record.region},${record.care_program},${record.outcome},${record.insurance_type},${record.length_of_stay}`)].join("\n");
  const form=new FormData();form.append("upload",new Blob([csv],{type:"text/csv"}),"synthetic-health.csv");
  const response=await fetch(`${process.env.ANALYTICS_WORKER_URL}/v1/ingest?dataset_id=${datasetId}`,{method:"POST",headers:{"x-worker-token":process.env.ANALYTICS_WORKER_TOKEN||""},body:form});
  const body=await response.json().catch(()=>({}));
  if(!response.ok)return {error:body.detail||"The encrypted demo dataset could not be created.",count:0,storageKey:""};
  return {error:undefined,count:records.length,storageKey:String(body.storage_key)};
}
