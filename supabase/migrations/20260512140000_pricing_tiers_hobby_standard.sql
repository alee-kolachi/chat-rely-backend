-- Pricing catalog: Free / Hobby / Standard / Pro (May 2026).
-- Slugs starter→hobby and growth→standard preserve plan row UUIDs (existing subscriptions keep plan_id).
-- Overage column is integer cents per conversation; display rates $0.145 / $0.099 / $0.080 are approximated as
-- 15 / 10 / 8 cents until fractional billing is supported. Marketing copy uses static UI for exact strings.
-- Scale is retired from checkout and public pricing; existing Scale subscribers keep this plan row until migrated.

update public.plans
set
  slug = 'hobby',
  name = 'Hobby',
  monthly_price_cents = 2900,
  included_conversations = 200,
  overage_conversation_cents = 15,
  max_agents = 1,
  features = jsonb_build_object(
    'shopify_enabled', true,
    'max_enabled_actions_per_agent', 3,
    'max_total_knowledge_mb', 15,
    'auto_retrain', false,
    'human_escalation_enabled', true,
    'pricing_display_overage_per_conversation_usd', '0.145',
    'pricing_card_bullets', jsonb_build_array(
      '200 billable conversations / month',
      '1 agent, 3 AI actions',
      '15 MB training content',
      'Shopify'
    )
  ),
  throttle_policy = '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2500, "strong_delay_ms": 8000}'::jsonb,
  is_active = true,
  public_on_pricing_page = true,
  sort_order = 20,
  updated_at = now()
where slug = 'starter';

update public.plans
set
  slug = 'standard',
  name = 'Standard',
  monthly_price_cents = 9900,
  included_conversations = 1000,
  overage_conversation_cents = 10,
  max_agents = 2,
  features = jsonb_build_object(
    'shopify_enabled', true,
    'max_enabled_actions_per_agent', 5,
    'max_total_knowledge_mb', 40,
    'auto_retrain', true,
    'human_escalation_enabled', true,
    'pricing_display_overage_per_conversation_usd', '0.099',
    'pricing_card_bullets', jsonb_build_array(
      '1,000 billable conversations / month',
      '2 agents, 5 AI actions each',
      '40 MB training content',
      'Auto retrain, advanced analytics'
    )
  ),
  throttle_policy = '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 2000, "strong_delay_ms": 6000}'::jsonb,
  is_active = true,
  public_on_pricing_page = true,
  sort_order = 30,
  updated_at = now()
where slug = 'growth';

update public.plans
set
  name = 'Pro',
  monthly_price_cents = 39900,
  included_conversations = 5000,
  overage_conversation_cents = 8,
  max_agents = 5,
  features = jsonb_build_object(
    'shopify_enabled', true,
    'max_enabled_actions_per_agent', 8,
    'max_total_knowledge_mb', 100,
    'auto_retrain', true,
    'human_escalation_enabled', true,
    'pricing_display_overage_per_conversation_usd', '0.080',
    'pricing_card_bullets', jsonb_build_array(
      '5,000 billable conversations / month',
      '5 agents, 8 AI actions each',
      '100 MB training content',
      'Source suggestions, remove branding'
    )
  ),
  throttle_policy = '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 1500, "strong_delay_ms": 5000}'::jsonb,
  is_active = true,
  public_on_pricing_page = true,
  sort_order = 40,
  updated_at = now()
where slug = 'pro';

update public.plans
set
  name = 'Free',
  monthly_price_cents = 0,
  included_conversations = 30,
  overage_conversation_cents = 0,
  max_agents = 1,
  features = jsonb_build_object(
    'shopify_enabled', false,
    'max_enabled_actions_per_agent', 0,
    'max_total_knowledge_mb', 1,
    'max_knowledge_storage_kb', 500,
    'max_website_crawl_kb', 500,
    'auto_retrain', false,
    'human_escalation_enabled', true,
    'pricing_display_overage_per_conversation_usd', '0.000',
    'pricing_card_bullets', jsonb_build_array(
      '30 billable conversations / month',
      '1 agent',
      '500 KB training content',
      'Limited models'
    )
  ),
  throttle_policy = '{"soft_overage_ratio": 1.0, "strong_overage_ratio": 1.2, "soft_delay_ms": 3000, "strong_delay_ms": 10000}'::jsonb,
  is_active = true,
  public_on_pricing_page = true,
  sort_order = 10,
  updated_at = now()
where slug = 'free';

update public.plans
set
  is_active = true,
  public_on_pricing_page = false,
  updated_at = now()
where slug = 'scale';
