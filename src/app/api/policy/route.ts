import { NextResponse } from "next/server";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { createAdminClient } from "@/lib/supabase/admin";

// max_contributions is optional on PATCH -- callers built before contribution
// bounding existed (and any client whose GET happened not to have populated
// it yet) still send only epsilon_total/delta_total/min_group_size, and that
// must keep working unchanged.
const updateSchema=z.object({organizationId:z.string().uuid(),datasetId:z.string().uuid(),epsilon_total:z.number().positive().max(100),delta_total:z.number().min(0).max(0.999999),min_group_size:z.number().int().min(1).max(1000),max_contributions:z.number().int().min(1).max(100).optional()});

async function context(organizationId:string,datasetId:string){const session=await createClient();const {data:{user}}=await session.auth.getUser();if(!user)return null;const admin=createAdminClient();const [{data:member},{data:dataset}]=await Promise.all([admin.from("organization_members").select("role").eq("organization_id",organizationId).eq("user_id",user.id).maybeSingle(),admin.from("datasets").select("id").eq("id",datasetId).eq("organization_id",organizationId).maybeSingle()]);return member&&dataset?{user,admin,role:member.role}:null;}

export async function GET(request:Request){const url=new URL(request.url);const organizationId=url.searchParams.get("organizationId")||"";const datasetId=url.searchParams.get("datasetId")||"";const current=await context(organizationId,datasetId);if(!current)return NextResponse.json({error:"Dataset access required"},{status:403});
// row_restrictions is exposed only as a count here -- an owner already knows
// their own configuration, but nothing forces this response to stay
// owner-only in the future, so the predicate content still stays out of it.
const {data,error}=await current.admin.from("privacy_policies").select("epsilon_total,epsilon_used,delta_total,delta_used,min_group_size,allowed_query_types,max_groups,privacy_unit,public_min_denominator,entity_column,max_contributions,row_restrictions").eq("dataset_id",datasetId).maybeSingle();
// A dataset row whose policy row never landed is a real state (an upload that
// half-failed), and `.single()` surfaced it as PostgREST's "Cannot coerce the
// result to a single JSON object" -- an internal driver message that tells an
// operator nothing about which dataset is broken or what to do next.
if(error)return NextResponse.json({error:error.message},{status:400});
if(!data)return NextResponse.json({error:"This dataset has no privacy policy, so it cannot be queried. It was most likely left behind by an upload that did not finish; delete it and upload the file again."},{status:409});
const {row_restrictions,...rest}=data;return NextResponse.json({...rest,row_restrictions_count:Array.isArray(row_restrictions)?row_restrictions.length:0});}

export async function PATCH(request:Request){const parsed=updateSchema.safeParse(await request.json().catch(()=>null));if(!parsed.success)return NextResponse.json({error:"Enter a valid privacy policy."},{status:400});const current=await context(parsed.data.organizationId,parsed.data.datasetId);if(!current||!["owner","admin"].includes(current.role))return NextResponse.json({error:"Owner or admin access required"},{status:403});const {data:existing}=await current.admin.from("privacy_policies").select("epsilon_used,delta_used").eq("dataset_id",parsed.data.datasetId).single();if(!existing)return NextResponse.json({error:"Privacy policy not found"},{status:404});if(parsed.data.epsilon_total<Number(existing.epsilon_used))return NextResponse.json({error:"Total epsilon cannot be lower than the amount already spent."},{status:409});if(parsed.data.delta_total<Number(existing.delta_used))return NextResponse.json({error:"Total delta cannot be lower than the amount already spent."},{status:409});const update:Record<string,unknown>={epsilon_total:parsed.data.epsilon_total,delta_total:parsed.data.delta_total,min_group_size:parsed.data.min_group_size};if(parsed.data.max_contributions!==undefined)update.max_contributions=parsed.data.max_contributions;const {error}=await current.admin.from("privacy_policies").update(update).eq("dataset_id",parsed.data.datasetId);if(error)return NextResponse.json({error:error.message},{status:400});await current.admin.from("audit_events").insert({organization_id:parsed.data.organizationId,actor_user_id:current.user.id,event_type:"privacy_policy.updated",resource_type:"dataset",resource_id:parsed.data.datasetId,event_metadata:update});return NextResponse.json({updated:true});}
