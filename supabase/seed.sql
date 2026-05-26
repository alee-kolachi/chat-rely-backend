-- Baseline plans for local development (kept in sync with migrations).
-- Safe to re-run because of ON CONFLICT on slug.

insert into public.plans (
  slug,
  name,
  monthly_price_cents,
  included_conversations,
  overage_conversation_cents,
  max_agents,
  features,
  throttle_policy,
  is_active,
  public_on_pricing_page,
  sort_order
)
values
  (
    'free',
    'Free',
    0,
    30,
    0,
    1,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 0, "max_total_knowledge_mb": 1, "max_knowledge_storage_kb": 500, "max_website_crawl_kb": 500, "auto_retrain": false, "human_escalation_enabled": true, "default_chat_model": "gpt-4o-mini", "premium_chat_model": "gpt-4o", "included_premium_turns": 0, "pricing_display_overage_per_conversation_usd": "0.000", "pricing_card_bullets": ["30 conversations / month", "1 agent · Shopify connect", "500 KB training content", "Essential AI — always on"]}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 3000, "strong_delay_ms": 10000}'::jsonb,
    true,
    true,
    10
  ),
  (
    'hobby',
    'Hobby',
    2900,
    250,
    15,
    1,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 3, "max_total_knowledge_mb": 15, "auto_retrain": false, "human_escalation_enabled": true, "default_chat_model": "gpt-4o-mini", "premium_chat_model": "gpt-4o", "included_premium_turns": 0, "pricing_display_overage_per_conversation_usd": "0.145", "pricing_card_bullets": ["250 conversations / month", "1 agent, 3 AI actions", "15 MB training content", "Shopify · always on"]}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2500, "strong_delay_ms": 8000}'::jsonb,
    true,
    true,
    20
  ),
  (
    'standard',
    'Standard',
    9900,
    1000,
    10,
    2,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 5, "max_total_knowledge_mb": 40, "auto_retrain": true, "human_escalation_enabled": true, "default_chat_model": "gpt-4o-mini", "premium_chat_model": "gpt-4o", "included_premium_turns": 250, "pricing_display_overage_per_conversation_usd": "0.099", "pricing_card_bullets": ["1,000 conversations / month", "Smart resolution for complex issues", "2 agents, 5 AI actions each", "40 MB training content", "Always on — may slow during heavy use"]}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2000, "strong_delay_ms": 6000}'::jsonb,
    true,
    true,
    30
  ),
  (
    'pro',
    'Pro',
    39900,
    5000,
    8,
    5,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 8, "max_total_knowledge_mb": 100, "auto_retrain": true, "human_escalation_enabled": true, "default_chat_model": "gpt-4o-mini", "premium_chat_model": "gpt-4o", "included_premium_turns": 800, "pricing_display_overage_per_conversation_usd": "0.080", "pricing_card_bullets": ["5,000 conversations / month", "Smart resolution · visitor feedback", "5 agents, 8 AI actions each", "100 MB training content", "Always on — may slow during heavy use"]}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1500, "strong_delay_ms": 5000}'::jsonb,
    true,
    true,
    40
  ),
  (
    'scale',
    'Scale',
    89900,
    20000,
    6,
    25,
    '{"shopify_enabled": true, "max_enabled_actions_per_agent": 24, "max_total_knowledge_mb": 500, "auto_retrain": true, "human_escalation_enabled": true, "default_chat_model": "gpt-4o-mini", "premium_chat_model": "gpt-4o", "included_premium_turns": 800, "pricing_card_bullets": ["Legacy enterprise tier"]}'::jsonb,
    '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1200, "strong_delay_ms": 4000}'::jsonb,
    true,
    false,
    50
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
      public_on_pricing_page = excluded.public_on_pricing_page,
      sort_order = excluded.sort_order,
      updated_at = now();
