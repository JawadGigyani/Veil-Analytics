import { createAdminClient } from "@/lib/supabase/admin";

export async function sharedRateLimit(actor:string,action:string,limit:number,windowSeconds:number){
  try { const {data,error}=await createAdminClient().rpc("consume_rate_limit",{target_actor:actor,target_action:action,target_limit:limit,target_window_seconds:windowSeconds}); if(error)return false; return data===true; }
  catch { return false; }
}
