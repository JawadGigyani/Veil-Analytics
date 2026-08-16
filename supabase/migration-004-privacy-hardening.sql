-- Reservations consume budget immediately. Failed executions are finalized as
-- failed without refunds because protected data may already have been read.
alter table public.privacy_policies
  add column if not exists allowed_query_types text[] not null default array['count','grouped_count','mean','bounded_sum','histogram','top_k'],
  add column if not exists max_groups integer not null default 20,
  add column if not exists privacy_unit text not null default 'row',
  add column if not exists public_min_denominator integer not null default 10,
  add column if not exists public_categories jsonb not null default '{}'::jsonb;

alter table public.queries add column if not exists error_message text;
alter table public.queries add column if not exists finalized_at timestamptz;
alter table public.queries alter column released_result drop not null;
alter table public.queries alter column status set default 'reserved';

do $$ begin alter table public.privacy_policies add constraint privacy_policies_epsilon_total_positive check (epsilon_total > 0); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_epsilon_used_valid check (epsilon_used >= 0 and epsilon_used <= epsilon_total); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_delta_total_valid check (delta_total >= 0 and delta_total < 1); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_min_group_size_positive check (min_group_size > 0); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_max_groups_valid check (max_groups > 0 and max_groups <= 100); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_min_denominator_positive check (public_min_denominator > 0); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_privacy_unit_nonempty check (length(btrim(privacy_unit)) > 0); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_policies add constraint privacy_policies_allowed_types_valid check (cardinality(allowed_query_types) > 0 and allowed_query_types <@ array['count','grouped_count','mean','bounded_sum','histogram','top_k']::text[]); exception when duplicate_object then null; end $$;
do $$ begin alter table public.queries add constraint queries_epsilon_spent_positive check (epsilon_spent > 0); exception when duplicate_object then null; end $$;
do $$ begin alter table public.queries add constraint queries_status_valid check (status in ('reserved','completed','failed')); exception when duplicate_object then null; end $$;
do $$ begin alter table public.privacy_ledger add constraint privacy_ledger_epsilon_spent_positive check (epsilon_spent > 0); exception when duplicate_object then null; end $$;

create or replace function public.reject_append_only_mutation() returns trigger language plpgsql set search_path=public as $$
begin raise exception '% is append-only', tg_table_name using errcode='55000'; end; $$;
drop trigger if exists privacy_ledger_append_only on public.privacy_ledger;
create trigger privacy_ledger_append_only before update or delete on public.privacy_ledger for each row execute function public.reject_append_only_mutation();
drop trigger if exists audit_events_append_only on public.audit_events;
create trigger audit_events_append_only before update or delete on public.audit_events for each row execute function public.reject_append_only_mutation();
revoke insert,update,delete on public.privacy_ledger from public,anon,authenticated;
revoke insert,update,delete on public.audit_events from public,anon,authenticated;

drop function if exists public.reserve_privacy_budget(uuid,numeric,jsonb,jsonb,text);
create or replace function public.reserve_privacy_budget(target_query uuid,target_dataset uuid,target_actor uuid,target_epsilon numeric,target_spec jsonb,target_operation text)
returns uuid language plpgsql security definer set search_path=public as $$
declare policy_row public.privacy_policies%rowtype; existing_query public.queries%rowtype;
begin
  if target_query is null or target_actor is null or target_epsilon is null or target_epsilon <= 0 then raise exception 'Invalid privacy budget reservation'; end if;
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
  insert into public.queries(id,dataset_id,submitted_by,query_spec,status,epsilon_spent,released_result) values(target_query,target_dataset,target_actor,target_spec,'reserved',target_epsilon,null);
  update public.privacy_policies set epsilon_used=epsilon_used+target_epsilon where dataset_id=target_dataset;
  insert into public.privacy_ledger(dataset_id,query_id,actor_id,operation,epsilon_spent) values(target_dataset,target_query,target_actor,target_operation,target_epsilon);
  return target_query;
end; $$;

create or replace function public.finalize_privacy_query(target_query uuid,target_status text,target_result jsonb default null,target_error text default null)
returns void language plpgsql security definer set search_path=public as $$
begin
  if target_status not in ('completed','failed') then raise exception 'Invalid final query status'; end if;
  if target_status='completed' and target_result is null then raise exception 'Completed query requires a released result'; end if;
  update public.queries set status=target_status,released_result=case when target_status='completed' then target_result else null end,error_message=case when target_status='failed' then left(coalesce(target_error,'Execution failed'),1000) else null end,finalized_at=now() where id=target_query and status='reserved';
  if not found then raise exception 'Reserved query not found'; end if;
end; $$;
revoke execute on function public.reserve_privacy_budget(uuid,uuid,uuid,numeric,jsonb,text) from public,anon,authenticated;
revoke execute on function public.finalize_privacy_query(uuid,text,jsonb,text) from public,anon,authenticated;
grant execute on function public.reserve_privacy_budget(uuid,uuid,uuid,numeric,jsonb,text) to service_role;
grant execute on function public.finalize_privacy_query(uuid,text,jsonb,text) to service_role;

create or replace function public.consume_rate_limit(target_actor text,target_action text,target_limit integer,target_window_seconds integer)
returns boolean language plpgsql security definer set search_path=public as $$
declare current_count integer;
begin
  if target_actor='' or target_action='' or target_limit<=0 or target_window_seconds<=0 then raise exception 'Invalid rate limit parameters'; end if;
  perform pg_advisory_xact_lock(hashtextextended(target_actor||':'||target_action,0));
  delete from public.rate_limit_events where created_at<clock_timestamp()-make_interval(secs=>target_window_seconds);
  select count(*) into current_count from public.rate_limit_events where actor_key=target_actor and action=target_action and created_at>=clock_timestamp()-make_interval(secs=>target_window_seconds);
  if current_count>=target_limit then return false; end if;
  insert into public.rate_limit_events(actor_key,action) values(target_actor,target_action);
  return true;
end; $$;
revoke execute on function public.consume_rate_limit(text,text,integer,integer) from public,anon,authenticated;
grant execute on function public.consume_rate_limit(text,text,integer,integer) to service_role;
