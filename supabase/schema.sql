-- BASELINE ONLY -- this file is not the current schema.
--
-- schema.sql creates the original tables and an early version of
-- reserve_privacy_budget that migrations 001-005 then replace. Running it
-- alone leaves a database that the application will not work against:
-- migration 004 drops and rewrites the budget function, adds the policy
-- constraints and the append-only triggers, and migration 005 adds
-- dataset_permissions and datasets.revoked_at.
--
-- Correct install order: schema.sql, then migration-001 through
-- migration-005, in order. See README.md.
--
-- The reserve_privacy_budget definition at the bottom of this file is
-- superseded by the one in migration-004-privacy-hardening.sql. Do not read
-- it as the enforced budget logic.

create extension if not exists "uuid-ossp";

create type public.member_role as enum ('admin', 'owner', 'analyst');
create type public.dataset_status as enum ('protected', 'processing', 'archived');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default 'New member',
  created_at timestamptz not null default now()
);
create table public.organizations (
  id uuid primary key default uuid_generate_v4(), name text not null, created_at timestamptz not null default now()
);
create table public.organization_members (
  organization_id uuid references public.organizations(id) on delete cascade,
  user_id uuid references auth.users(id) on delete cascade,
  role public.member_role not null default 'analyst', created_at timestamptz not null default now(),
  primary key (organization_id, user_id)
);
create table public.datasets (
  id uuid primary key default uuid_generate_v4(), organization_id uuid not null references public.organizations(id) on delete cascade,
  name text not null, description text not null default '', row_count integer not null default 0, column_count integer not null default 0,
  status public.dataset_status not null default 'protected', created_by uuid references auth.users(id), created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create table public.privacy_policies (
  dataset_id uuid primary key references public.datasets(id) on delete cascade, epsilon_total numeric not null default 5,
  epsilon_used numeric not null default 0, delta_total numeric not null default 0.000001, min_group_size integer not null default 5
);
create table public.dataset_columns (
  id uuid primary key default uuid_generate_v4(), dataset_id uuid not null references public.datasets(id) on delete cascade,
  name text not null, data_type text not null, can_group boolean not null default false, can_measure boolean not null default false,
  lower_bound numeric, upper_bound numeric, unique(dataset_id, name)
);
create table public.synthetic_health_records (
  id uuid primary key default uuid_generate_v4(), dataset_id uuid not null references public.datasets(id) on delete cascade,
  age integer not null check (age between 18 and 90), region text not null, care_program text not null,
  outcome text not null, insurance_type text not null, length_of_stay numeric not null check (length_of_stay between 0 and 90),
  created_at timestamptz not null default now()
);
create table public.queries (
  id uuid primary key default uuid_generate_v4(), dataset_id uuid not null references public.datasets(id) on delete cascade,
  submitted_by uuid references auth.users(id), query_spec jsonb not null, status text not null default 'completed',
  epsilon_spent numeric not null, released_result jsonb not null, created_at timestamptz not null default now()
);
create table public.privacy_ledger (
  id uuid primary key default uuid_generate_v4(), dataset_id uuid not null references public.datasets(id) on delete cascade,
  query_id uuid not null references public.queries(id) on delete cascade, actor_id uuid references auth.users(id),
  operation text not null, epsilon_spent numeric not null, created_at timestamptz not null default now()
);

create or replace function public.is_org_member(target_org uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists(select 1 from public.organization_members where organization_id = target_org and user_id = auth.uid());
$$;

alter table public.profiles enable row level security;
alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.datasets enable row level security;
alter table public.privacy_policies enable row level security;
alter table public.dataset_columns enable row level security;
alter table public.queries enable row level security;
alter table public.privacy_ledger enable row level security;
alter table public.synthetic_health_records enable row level security;

create policy "members read organizations" on public.organizations for select using (public.is_org_member(id));
create policy "members read memberships" on public.organization_members for select using (user_id = auth.uid() or public.is_org_member(organization_id));
create policy "members read datasets" on public.datasets for select using (public.is_org_member(organization_id));
create policy "members read policies" on public.privacy_policies for select using (exists(select 1 from public.datasets d where d.id=dataset_id and public.is_org_member(d.organization_id)));
create policy "members read columns" on public.dataset_columns for select using (exists(select 1 from public.datasets d where d.id=dataset_id and public.is_org_member(d.organization_id)));
create policy "members read own queries" on public.queries for select using (submitted_by=auth.uid() or exists(select 1 from public.datasets d where d.id=dataset_id and public.is_org_member(d.organization_id)));
create policy "members read ledger" on public.privacy_ledger for select using (exists(select 1 from public.datasets d where d.id=dataset_id and public.is_org_member(d.organization_id)));
-- Deliberately no SELECT policy: raw records are server-only and cannot be read with the browser key.
revoke all on public.synthetic_health_records from anon, authenticated;
revoke insert, update, delete on public.privacy_ledger from anon, authenticated;
revoke insert, update, delete on public.queries from anon, authenticated;

-- SUPERSEDED by migration-004-privacy-hardening.sql, which drops this
-- signature entirely and replaces it with a version that takes a caller-
-- supplied query id, validates the operation against allowed_query_types,
-- and separates reservation from finalization. This definition is kept only
-- so the baseline file applies cleanly on an empty database.
create or replace function public.reserve_privacy_budget(
  target_dataset uuid, target_epsilon numeric, target_spec jsonb, target_result jsonb, target_operation text
) returns uuid language plpgsql security definer set search_path = public as $$
declare policy_row public.privacy_policies%rowtype; new_query uuid;
begin
  select * into policy_row from public.privacy_policies where dataset_id=target_dataset for update;
  if policy_row.dataset_id is null then raise exception 'Privacy policy not found'; end if;
  if not exists(select 1 from public.datasets d where d.id=target_dataset and public.is_org_member(d.organization_id)) then raise exception 'Not authorized'; end if;
  if policy_row.epsilon_used + target_epsilon > policy_row.epsilon_total then raise exception 'Insufficient privacy budget'; end if;
  insert into public.queries(dataset_id,submitted_by,query_spec,epsilon_spent,released_result) values(target_dataset,auth.uid(),target_spec,target_epsilon,target_result) returning id into new_query;
  update public.privacy_policies set epsilon_used=epsilon_used+target_epsilon where dataset_id=target_dataset;
  insert into public.privacy_ledger(dataset_id,query_id,actor_id,operation,epsilon_spent) values(target_dataset,new_query,auth.uid(),target_operation,target_epsilon);
  return new_query;
end; $$;

revoke execute on function public.reserve_privacy_budget(uuid,numeric,jsonb,jsonb,text) from public;
grant execute on function public.reserve_privacy_budget(uuid,numeric,jsonb,jsonb,text) to authenticated;

insert into public.organizations(name) values ('Demo Care Collaborative');
