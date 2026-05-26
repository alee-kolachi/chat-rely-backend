-- Enforce starter plan knowledge storage and crawl budget caps to 500 KB.
-- This explicitly sets max_knowledge_storage_kb and max_website_crawl_kb
-- so backend logic does not fall back to larger defaults.

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
