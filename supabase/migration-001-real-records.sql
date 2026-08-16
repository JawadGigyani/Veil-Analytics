-- Run this migration in an existing project after the original schema.sql.
create table if not exists public.synthetic_health_records (
  id uuid primary key default uuid_generate_v4(), dataset_id uuid not null references public.datasets(id) on delete cascade,
  age integer not null check (age between 18 and 90), region text not null, care_program text not null,
  outcome text not null, insurance_type text not null, length_of_stay numeric not null check (length_of_stay between 0 and 90),
  created_at timestamptz not null default now()
);
alter table public.synthetic_health_records enable row level security;
drop policy if exists "members read memberships" on public.organization_members;
create policy "members read memberships" on public.organization_members for select using (user_id = auth.uid() or public.is_org_member(organization_id));
revoke all on public.synthetic_health_records from anon, authenticated;
revoke insert, update, delete on public.privacy_ledger from anon, authenticated;
revoke insert, update, delete on public.queries from anon, authenticated;
revoke execute on function public.reserve_privacy_budget(uuid,numeric,jsonb,jsonb,text) from public;
grant execute on function public.reserve_privacy_budget(uuid,numeric,jsonb,jsonb,text) to authenticated;
