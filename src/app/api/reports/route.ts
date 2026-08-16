import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { advancedComposition, sequentialComposition } from "@/lib/accounting";

export async function GET(request:Request){
  const supabase=await createClient();const {data:{user}}=await supabase.auth.getUser();if(!user)return NextResponse.json({error:"Unauthorized"},{status:401});
  const requested=new URL(request.url).searchParams.get("organizationId");let membershipQuery=supabase.from("organization_members").select("organization_id").eq("user_id",user.id);if(requested)membershipQuery=membershipQuery.eq("organization_id",requested);const {data:memberships}=await membershipQuery.order("created_at",{ascending:true}).limit(1);const membership=memberships?.[0];if(!membership)return NextResponse.json({error:"Workspace not initialized"},{status:409});
  const {data:datasets}=await supabase.from("datasets").select("id,name,row_count,column_count,created_at").eq("organization_id",membership.organization_id);const ids=(datasets||[]).map(dataset=>dataset.id);const {data:ledger}=ids.length?await supabase.from("privacy_ledger").select("operation,epsilon_spent,delta_spent,created_at,dataset_id").in("dataset_id",ids).order("created_at",{ascending:false}):{data:[]};
  const {data:policies}=ids.length?await supabase.from("privacy_policies").select("dataset_id,epsilon_total,epsilon_used,delta_total,delta_used").in("dataset_id",ids):{data:[]};

  // `epsilon_used` (and, since migration-007, `delta_used`) are the counters
  // the budget check enforces against; the append-only ledger is the record
  // of how they got there. Nothing keeps them in step automatically, so the
  // report states whether they agree. A divergence means budget was spent or
  // reset outside the reservation path and the enforced remaining budget can
  // no longer be derived from evidence.
  const reconciliation=(policies||[]).map(policy=>{
    const datasetLedger=(ledger||[]).filter(event=>event.dataset_id===policy.dataset_id);
    const ledgerSum=datasetLedger.reduce((total,event)=>total+Number(event.epsilon_spent),0);
    const counter=Number(policy.epsilon_used);
    const drift=Number((counter-ledgerSum).toFixed(10));
    const deltaLedgerSum=datasetLedger.reduce((total,event)=>total+Number(event.delta_spent),0);
    const deltaCounter=Number(policy.delta_used);
    const deltaDrift=Number((deltaCounter-deltaLedgerSum).toFixed(10));
    return {dataset_id:policy.dataset_id,epsilon_total:Number(policy.epsilon_total),epsilon_used_counter:counter,epsilon_sum_from_ledger:Number(ledgerSum.toFixed(10)),drift,reconciled:Math.abs(drift)<1e-9,delta_total:Number(policy.delta_total),delta_used_counter:deltaCounter,delta_sum_from_ledger:Number(deltaLedgerSum.toFixed(10)),delta_drift:deltaDrift,delta_reconciled:Math.abs(deltaDrift)<1e-9};
  });

  const epsilons=(ledger||[]).map(event=>Number(event.epsilon_spent));const body={generated_at:new Date().toISOString(),organization_id:membership.organization_id,datasets,release_count:ledger?.length||0,accounting:{enforced_method:"sequential",sequential_epsilon:sequentialComposition(epsilons),advanced_composition_diagnostic:advancedComposition(epsilons,1e-6),diagnostic_delta:1e-6},budget_reconciliation:{all_reconciled:reconciliation.every(entry=>entry.reconciled&&entry.delta_reconciled),note:"epsilon_used/delta_used are the enforced counters; epsilon_sum_from_ledger/delta_sum_from_ledger are the append-only record. A non-zero drift means budget moved outside the reservation path.",per_dataset:reconciliation},releases:ledger};
  return new NextResponse(JSON.stringify(body,null,2),{headers:{"Content-Type":"application/json","Content-Disposition":"attachment; filename=veil-privacy-report.json"}});
}
