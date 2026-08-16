-- migration-005-permissions-audit.sql
-- Adds per-user dataset permissions, dataset revocation, and query hash deduplication.

-- Per-user dataset permissions for fine-grained access control
create table if not exists public.dataset_permissions (
  dataset_id uuid not null references public.datasets(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  permission text not null check (permission in ('view_schema', 'run_queries', 'manage_dataset', 'view_audit_log')),
  granted_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  primary key (dataset_id, user_id, permission)
);
alter table public.dataset_permissions enable row level security;

drop policy if exists "members read dataset permissions" on public.dataset_permissions;
create policy "members read dataset permissions" on public.dataset_permissions
  for select using (
    exists(
      select 1 from public.datasets d
      where d.id = dataset_id
        and public.is_org_member(d.organization_id)
    )
  );

-- Only service_role can manage permissions directly
revoke insert, update, delete on public.dataset_permissions from public, anon, authenticated;

-- Dataset revocation: add revoked_at timestamp
alter table public.datasets add column if not exists revoked_at timestamptz;

-- Query hash for deduplication
alter table public.queries add column if not exists normalized_query_hash text;
create index if not exists queries_hash_idx on public.queries(dataset_id, normalized_query_hash)
  where normalized_query_hash is not null;

-- Audit events: allow reading from authenticated role (already has RLS)
-- Add index for faster org-scoped queries
create index if not exists audit_events_org_idx on public.audit_events(organization_id, created_at desc);
