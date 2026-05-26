-- Website source: allow skipped_duplicate status (no indexing job).
-- Plan feature: per-crawl HTTP byte budget (see backend _website_crawl_budget_bytes).

do $$
begin
  if not exists (
    select 1
    from pg_enum e
    join pg_type t on e.enumtypid = t.oid
    join pg_namespace n on t.typnamespace = n.oid
    where n.nspname = 'public'
      and t.typname = 'knowledge_source_status'
      and e.enumlabel = 'skipped_duplicate'
  ) then
    alter type public.knowledge_source_status add value 'skipped_duplicate';
  end if;
end
$$;

update public.plans
set features = coalesce(features, '{}'::jsonb) || '{"max_website_crawl_kb": 500}'::jsonb,
    updated_at = now()
where slug = 'free';

update public.plans
set features = coalesce(features, '{}'::jsonb) || '{"max_website_crawl_kb": 10240}'::jsonb,
    updated_at = now()
where slug is not null
  and slug <> 'free';
