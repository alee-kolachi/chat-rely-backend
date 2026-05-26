-- Merchant-facing conversation summaries (dashboard conversations page).

create table if not exists public.conversation_summaries (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null unique references public.conversations(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  summary text not null default '',
  key_points jsonb not null default '[]'::jsonb check (jsonb_typeof(key_points) = 'array'),
  message_count int not null default 0,
  computed_at timestamptz not null default now(),
  model text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists conversation_summaries_user_agent_idx
  on public.conversation_summaries (user_id, agent_id);

comment on table public.conversation_summaries is
  'LLM-generated merchant summary of a support thread (dashboard quick-read).';

alter table public.conversation_summaries enable row level security;

create policy conversation_summaries_manage_own
  on public.conversation_summaries
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and public.is_owner_of_agent(agent_id)
  );
