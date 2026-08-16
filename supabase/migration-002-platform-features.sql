alter table public.datasets add column if not exists storage_key text;
alter table public.datasets add column if not exists file_format text;
alter table public.privacy_policies add column if not exists accounting_method text not null default 'sequential' check (accounting_method in ('sequential','rdp'));
alter table public.privacy_policies add column if not exists delta_used numeric not null default 0 check (delta_used >= 0);
alter table public.privacy_ledger add column if not exists delta_spent numeric not null default 0 check (delta_spent >= 0);
create table if not exists public.audit_events (
  id uuid primary key default uuid_generate_v4(), organization_id uuid not null references public.organizations(id) on delete cascade,
  actor_user_id uuid references auth.users(id), event_type text not null, resource_type text not null, resource_id uuid,
  event_metadata jsonb not null default '{}'::jsonb, created_at timestamptz not null default now()
);
alter table public.audit_events enable row level security;
drop policy if exists "members read audit events" on public.audit_events;
create policy "members read audit events" on public.audit_events for select using (public.is_org_member(organization_id));
revoke insert,update,delete on public.audit_events from anon,authenticated;
insert into storage.buckets (id,name,public,file_size_limit,allowed_mime_types)
values ('protected-datasets','protected-datasets',false,25000000,array['application/octet-stream'])
on conflict (id) do update set public=false,file_size_limit=25000000;
-- No browser storage policies are created. Only the service role can read or write encrypted dataset objects.
