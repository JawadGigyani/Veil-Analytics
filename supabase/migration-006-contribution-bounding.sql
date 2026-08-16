-- migration-006-contribution-bounding.sql
-- Adds contribution bounding: the entity column that identifies the privacy
-- unit, and the per-entity row cap every sensitivity calculation must scale
-- by. Every release issued so far assumed one row per person -- privacy_unit
-- was recorded as 'row' and never enforced. If a dataset has more than one
-- row per person, every epsilon spent against it understates the true
-- sensitivity by a factor of the largest per-person contribution.

-- entity_column is nullable: null means the row itself is the privacy unit,
-- which is today's (unenforced) assumption and stays the default. When an
-- owner nominates an entity_column at ingestion, the privacy unit becomes
-- that entity and max_contributions bounds how many rows a single entity may
-- contribute before aggregation -- the executor and sensitivity layers
-- (Wave 2, Python side) consume both fields. max_contributions defaults to 1
-- so an unset policy reproduces current behaviour exactly.
alter table public.privacy_policies
  add column if not exists entity_column text,
  add column if not exists max_contributions integer not null default 1;

do $$ begin alter table public.privacy_policies add constraint privacy_policies_max_contributions_valid check (max_contributions between 1 and 100); exception when duplicate_object then null; end $$;
