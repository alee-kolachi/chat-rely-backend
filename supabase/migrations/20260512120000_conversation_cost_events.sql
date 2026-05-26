-- Append-only ledger for true per-conversation spend (LLM completions, RAG embeddings, $0 tool rows).
-- Written by the FastAPI backend (service role). RLS mirrors `messages` for direct Supabase reads.

create table if not exists public.conversation_cost_events (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  turn_user_message_id uuid references public.messages(id) on delete set null,
  kind text not null,
  provider_model text,
  input_tokens integer not null default 0 check (input_tokens >= 0),
  output_tokens integer not null default 0 check (output_tokens >= 0),
  embedding_tokens integer not null default 0 check (embedding_tokens >= 0),
  cost_usd double precision,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists conversation_cost_events_conversation_id_created_at_idx
  on public.conversation_cost_events(conversation_id, created_at);

create index if not exists conversation_cost_events_turn_user_message_id_idx
  on public.conversation_cost_events(turn_user_message_id)
  where turn_user_message_id is not null;

create index if not exists conversation_cost_events_user_id_created_at_idx
  on public.conversation_cost_events(user_id, created_at desc);

alter table public.conversation_cost_events enable row level security;

create policy conversation_cost_events_select_own
  on public.conversation_cost_events
  for select
  to authenticated
  using (
    user_id = auth.uid()
    and exists (
      select 1
      from public.conversations c
      where c.id = conversation_id
        and c.user_id = auth.uid()
    )
  );

create policy conversation_cost_events_insert_own
  on public.conversation_cost_events
  for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and exists (
      select 1
      from public.conversations c
      where c.id = conversation_id
        and c.user_id = auth.uid()
    )
    and public.is_owner_of_agent(agent_id)
  );

-- No update/delete: append-only ledger (service role bypasses RLS for backend writes).

comment on table public.conversation_cost_events is
  'Per-operation cost lines for runtime (LLM, embeddings, tool placeholders). Admin totals; optional client read via RLS.';
