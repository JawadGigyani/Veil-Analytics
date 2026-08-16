-- migration-007-delta-accounting.sql
-- Delta accounting. privacy_policies.delta_total/delta_used and
-- privacy_ledger.delta_spent have existed since migration-002, but nothing
-- has ever incremented or checked them -- every (epsilon, delta) release has
-- instead been refused outright, above this layer, since the platform could
-- not otherwise guarantee delta was honestly spent. This migration makes
-- reserve_privacy_budget spend and check delta with exactly the rigour
-- epsilon already gets: same row lock, same transaction, same style of
-- constraint. Gaussian stays refused above this layer (Zod schema, API
-- route, worker) until each of those is updated to match -- this migration
-- only makes the enforcement possible, it does not by itself turn Gaussian
-- back on anywhere.

do $$ begin alter table public.privacy_policies add constraint privacy_policies_delta_used_valid check (delta_used >= 0 and delta_used <= delta_total); exception when duplicate_object then null; end $$;

-- Old signature took no delta parameter at all; drop it explicitly before
-- recreating under the new signature, following migration-004's pattern.
drop function if exists public.reserve_privacy_budget(uuid,uuid,uuid,numeric,jsonb,text);
create or replace function public.reserve_privacy_budget(target_query uuid,target_dataset uuid,target_actor uuid,target_epsilon numeric,target_spec jsonb,target_operation text,target_delta numeric default 0)
returns uuid language plpgsql security definer set search_path=public as $$
declare policy_row public.privacy_policies%rowtype; existing_query public.queries%rowtype;
begin
  if target_query is null or target_actor is null or target_epsilon is null or target_epsilon<=0 or target_delta is null or target_delta<0 then raise exception 'Invalid privacy budget reservation'; end if;
  select * into existing_query from public.queries where id=target_query;
  if found then
    if existing_query.dataset_id<>target_dataset or existing_query.submitted_by<>target_actor or existing_query.epsilon_spent<>target_epsilon or existing_query.query_spec<>target_spec then raise exception 'Reservation id conflicts with an existing query'; end if;
    return existing_query.id;
  end if;
  select p.* into policy_row from public.privacy_policies p join public.datasets d on d.id=p.dataset_id where p.dataset_id=target_dataset and d.status='protected' for update of p;
  if not found then raise exception 'Protected dataset privacy policy not found'; end if;
  if not exists(select 1 from public.datasets d join public.organization_members m on m.organization_id=d.organization_id where d.id=target_dataset and m.user_id=target_actor) then raise exception 'Not authorized'; end if;
  if not (target_operation=any(policy_row.allowed_query_types)) then raise exception 'Query type is not allowed by this privacy policy'; end if;
  if policy_row.epsilon_used+target_epsilon>policy_row.epsilon_total then raise exception 'Insufficient privacy budget'; end if;
  if policy_row.delta_used+target_delta>policy_row.delta_total then raise exception 'Insufficient delta budget'; end if;
  insert into public.queries(id,dataset_id,submitted_by,query_spec,status,epsilon_spent,released_result) values(target_query,target_dataset,target_actor,target_spec,'reserved',target_epsilon,null);
  update public.privacy_policies set epsilon_used=epsilon_used+target_epsilon,delta_used=delta_used+target_delta where dataset_id=target_dataset;
  insert into public.privacy_ledger(dataset_id,query_id,actor_id,operation,epsilon_spent,delta_spent) values(target_dataset,target_query,target_actor,target_operation,target_epsilon,target_delta);
  return target_query;
end; $$;

-- A new signature does not inherit the old signature's grants -- both must
-- be re-issued or every query breaks the moment this migration applies.
revoke execute on function public.reserve_privacy_budget(uuid,uuid,uuid,numeric,jsonb,text,numeric) from public,anon,authenticated;
grant execute on function public.reserve_privacy_budget(uuid,uuid,uuid,numeric,jsonb,text,numeric) to service_role;
