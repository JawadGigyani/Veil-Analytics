-- migration-009-bootstrap-idempotency.sql
-- Makes automatic workspace provisioning idempotent at the database level.
--
-- POST /api/bootstrap reads the caller's organization_members rows and, when
-- it finds none, inserts a new organization. That is a check-then-act
-- sequence with nothing serialising it, and two callers fire it on a normal
-- sign-in: src/components/auth-screen.tsx POSTs it and then reloads the page,
-- and the reloaded src/app/page.tsx POSTs it again on mount. Provisioning is
-- slow -- it seeds 1,200 records through the analytics worker before the
-- membership row commits -- so the second request reliably observes "no
-- membership" while the first is still in flight and provisions a second
-- workspace. React's development double-mount fires it a third time.
--
-- Observed in a live workspace: five organizations, all identically named,
-- created inside a 19-second window, two of them 117 ms apart. The user then
-- has five workspaces in the organization picker, each holding its own copy
-- of the demo dataset with its own separate privacy budget, and the workspace
-- route silently pins them to whichever sorts first by created_at.
--
-- created_for_user records which user an organization was auto-provisioned
-- for, and the UNIQUE constraint makes the second concurrent insert fail
-- rather than succeed. Postgres permits many NULLs under a UNIQUE constraint,
-- so organizations a user creates deliberately through POST /api/organizations
-- leave this NULL and remain unlimited -- this constrains only the automatic
-- personal workspace, of which there should be exactly one per user.
alter table public.organizations
  add column if not exists created_for_user uuid references auth.users(id) on delete set null;

-- Partial index: only auto-provisioned rows participate, and the predicate
-- keeps the index small since deliberately-created organizations are NULL.
create unique index if not exists organizations_created_for_user_key
  on public.organizations (created_for_user)
  where created_for_user is not null;

comment on column public.organizations.created_for_user is
  'Set only by POST /api/bootstrap when auto-provisioning a personal workspace. The unique index on this column is what makes concurrent bootstrap calls collapse into one organization instead of racing. Organizations created deliberately via POST /api/organizations leave this NULL.';
