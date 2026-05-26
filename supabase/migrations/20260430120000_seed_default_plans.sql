-- Baseline catalog plans (required for bootstrap: slug = 'starter' must exist).
-- Mirrors supabase/seed.sql so `supabase db push` / migrate-only setups work without a separate seed step.

insert into public.plans (
  slug,
  name,
  monthly_price_cents,
  included_conversations,
  overage_conversation_cents,
  max_agents,
  features,
  throttle_policy,
  is_active
)
values
  (
    'starter',
    'Starter',
    3900,
    500,
    8,
    1,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 5, "max_file_storage_mb": 100, "max_knowledge_storage_kb": 500, "max_website_crawl_kb": 500, "auto_retrain": false}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2500, "strong_delay_ms": 8000}'::jsonb,
    true
  ),
  (
    'growth',
    'Growth',
    9900,
    2000,
    5,
    3,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 8, "max_file_storage_mb": 1000, "auto_retrain": true}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2000, "strong_delay_ms": 6000}'::jsonb,
    true
  ),
  (
    'pro',
    'Pro',
    24900,
    6000,
    4,
    8,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 12, "max_file_storage_mb": 5000, "auto_retrain": true}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1500, "strong_delay_ms": 5000}'::jsonb,
    true
  ),
  (
    'scale',
    'Scale',
    59900,
    20000,
    3,
    20,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 20, "max_file_storage_mb": 20000, "auto_retrain": true}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1200, "strong_delay_ms": 4000}'::jsonb,
    true
  )
on conflict (slug)
do update
  set name = excluded.name,
      monthly_price_cents = excluded.monthly_price_cents,
      included_conversations = excluded.included_conversations,
      overage_conversation_cents = excluded.overage_conversation_cents,
      max_agents = excluded.max_agents,
      features = excluded.features,
      throttle_policy = excluded.throttle_policy,
      is_active = excluded.is_active,
      updated_at = now();
