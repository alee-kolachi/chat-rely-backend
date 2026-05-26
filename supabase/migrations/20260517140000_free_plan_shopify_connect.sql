-- Free tier: allow Shopify OAuth connect; live store tools still gated by max_enabled_actions_per_agent (0).
update public.plans
set
  features = jsonb_set(
    coalesce(features, '{}'::jsonb) || '{"shopify_enabled": true}'::jsonb,
    '{pricing_card_bullets}',
    '["30 conversations / month", "1 agent · Shopify connect", "500 KB training content", "Essential AI — always on"]'::jsonb,
    true
  ),
  updated_at = now()
where slug = 'free';
