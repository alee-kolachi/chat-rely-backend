-- Outcomes from post-close LLM analysis (dashboard KPIs, training topics).

create table if not exists public.conversation_outcomes (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null unique references public.conversations(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  computed_at timestamptz not null default now(),
  model text not null default '',
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists conversation_outcomes_agent_computed_idx
  on public.conversation_outcomes (agent_id, computed_at desc);

create index if not exists conversation_outcomes_user_agent_idx
  on public.conversation_outcomes (user_id, agent_id);

create index if not exists conversation_outcomes_started_lookup_idx
  on public.conversation_outcomes (agent_id, conversation_id);

comment on table public.conversation_outcomes is
  'LLM-derived closure metrics: resolution, end_reason, training_topics (see payload schema in backend).';

alter table public.conversation_outcomes enable row level security;

create policy conversation_outcomes_manage_own
  on public.conversation_outcomes
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and public.is_owner_of_agent(agent_id)
  );
