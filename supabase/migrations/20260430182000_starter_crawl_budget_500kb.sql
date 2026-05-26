-- Ensure existing starter plans use 500 KB crawl budget and 500 KB knowledge storage.

update public.plans
set
  features = jsonb_set(
    jsonb_set(
      coalesce(features, '{}'::jsonb),
      '{max_knowledge_storage_kb}',
      to_jsonb(500),
      true
    ),
    '{max_website_crawl_kb}',
    to_jsonb(500),
    true
  ),
  updated_at = now()
where slug = 'starter';
