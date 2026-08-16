-- migration-008-sensitive-columns-row-restrictions.sql
-- Sensitive column marking and row-level restrictions -- the last two
-- data-owner controls from section 2 of the original project plan, deferred
-- out of the enhancement plan's Wave 2/3 because neither changes the privacy
-- guarantee and both were cheaper once contribution bounding (migration-006)
-- had settled the schema.

-- is_sensitive marks a column that may still be MEASURED -- a bounded sum,
-- mean, or histogram over it releases a noisy aggregate exactly as any other
-- numeric column would -- but must never be used as a group_by column or as
-- a filter column. Grouping or filtering on a sensitive attribute makes the
-- released structure itself disclosive: a group label or a filter predicate
-- reveals the attribute directly, no matter how much noise is added to the
-- aggregate released alongside it. This is the same reasoning that already
-- keeps analyst-supplied filter *values* out of the audit log (see
-- src/lib/audit.ts) -- here the column identity itself is the sensitive
-- part, so it cannot be allowed to select or partition the released rows.
-- Enforced in src/lib/domain.ts (shape) and src/app/api/query/route.ts
-- (against real column metadata), and re-validated independently by the
-- worker from the `sensitive_columns` list forwarded with every query.
alter table public.dataset_columns
  add column if not exists is_sensitive boolean not null default false;

-- row_restrictions is an owner-defined array of predicates, each shaped like
-- an analyst filter -- {column, operator, value} with operator restricted to
-- equals/not_equals -- that the worker ANDs into every query against this
-- dataset before aggregation. Unlike entity_column/max_contributions
-- (migration-006), a row restriction does not change sensitivity: removing
-- one privacy unit still changes an aggregate by the same amount whether or
-- not a restriction narrowed the rows first, so no noise calibration
-- changes here.
--
-- A row restriction is owner configuration, not query input, and may itself
-- encode a sensitive predicate -- "consent = true" is harmless to disclose,
-- "diagnosis != 'HIV'" is not. It must never appear in an error message, an
-- audit event, or any analyst-visible response: callers may report that a
-- restriction was applied, and how many, never its column, operator, or
-- value. Defaults to an empty array, which reproduces today's behaviour
-- (no restriction) exactly.
alter table public.privacy_policies
  add column if not exists row_restrictions jsonb not null default '[]'::jsonb;

do $$ begin
  alter table public.privacy_policies add constraint privacy_policies_row_restrictions_valid
    check (jsonb_typeof(row_restrictions) = 'array' and jsonb_array_length(row_restrictions) <= 10);
  exception when duplicate_object then null;
end $$;
