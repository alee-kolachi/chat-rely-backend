-- Free tier plan for default subscriptions and upgrade UX (knowledge limits via features JSON).

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
    'free',
    'Free',
    0,
    100,
    0,
    1,
    '{"shopify_enabled": false, "max_enabled_actions_per_agent": 2, "max_file_storage_mb": 10, "max_knowledge_storage_kb": 400, "max_website_crawl_kb": 500, "auto_retrain": false}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 3000, "strong_delay_ms": 10000}'::jsonb,
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
