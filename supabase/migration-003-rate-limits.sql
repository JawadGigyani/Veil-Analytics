create table if not exists public.rate_limit_events (
  id uuid primary key default uuid_generate_v4(), actor_key text not null, action text not null, created_at timestamptz not null default now()
);
create index if not exists rate_limit_events_lookup on public.rate_limit_events(actor_key, action, created_at);
alter table public.rate_limit_events enable row level security;
revoke all on public.rate_limit_events from anon, authenticated;
create or replace function public.consume_rate_limit(target_actor text, target_action text, target_limit integer, target_window_seconds integer)
returns boolean language plpgsql security definer set search_path=public as $$
declare current_count integer;
begin
  delete from public.rate_limit_events where created_at < now() - make_interval(secs => target_window_seconds);
  select count(*) into current_count from public.rate_limit_events where actor_key=target_actor and action=target_action and created_at >= now() - make_interval(secs => target_window_seconds);
  if current_count >= target_limit then return false; end if;
  insert into public.rate_limit_events(actor_key,action) values(target_actor,target_action);
  return true;
end; $$;
revoke execute on function public.consume_rate_limit(text,text,integer,integer) from public;
grant execute on function public.consume_rate_limit(text,text,integer,integer) to service_role;
