-- Visitor thumbs on assistant messages (Pro) + optional LLM summary cache per agent/date range.

create table if not exists public.message_visitor_feedback (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references public.messages(id) on delete cascade,
  visitor_id text not null,
  value smallint not null check (value in (-1, 1)),
  resolved_at timestamptz,
  resolved_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (message_id, visitor_id)
);

create index if not exists message_visitor_feedback_message_id_idx
  on public.message_visitor_feedback (message_id);

create index if not exists message_visitor_feedback_unresolved_down_idx
  on public.message_visitor_feedback (message_id)
  where value = -1 and resolved_at is null;

comment on table public.message_visitor_feedback is
  'Per-visitor thumbs on assistant messages; resolved_at applies to all rows for the same message_id when seller marks resolved.';

create table if not exists public.agent_feedback_summary (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  range_from timestamptz not null,
  range_to timestamptz not null,
  batch_index integer not null check (batch_index >= 0),
  summary text not null default '',
  topics jsonb not null default '[]'::jsonb
    check (jsonb_typeof(topics) = 'array'),
  model text,
  created_at timestamptz not null default now(),
  unique (agent_id, user_id, range_from, range_to, batch_index)
);

create index if not exists agent_feedback_summary_agent_range_idx
  on public.agent_feedback_summary (agent_id, user_id, range_from, range_to);

comment on table public.agent_feedback_summary is
  'Cached LLM digest for visitor thumbs-down batches (Pro analytics date range).';

create trigger set_message_visitor_feedback_updated_at
before update on public.message_visitor_feedback
for each row execute function public.set_updated_at();

alter table public.message_visitor_feedback enable row level security;
alter table public.agent_feedback_summary enable row level security;

-- Tenant can read/write feedback for messages they own (via messages.user_id).
create policy message_visitor_feedback_select_own
  on public.message_visitor_feedback
  for select
  to authenticated
  using (
    exists (
      select 1 from public.messages m
      where m.id = message_id and m.user_id = auth.uid()
    )
  );

create policy message_visitor_feedback_insert_own
  on public.message_visitor_feedback
  for insert
  to authenticated
  with check (
    exists (
      select 1 from public.messages m
      where m.id = message_id and m.user_id = auth.uid()
    )
  );

create policy message_visitor_feedback_update_own
  on public.message_visitor_feedback
  for update
  to authenticated
  using (
    exists (
      select 1 from public.messages m
      where m.id = message_id and m.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.messages m
      where m.id = message_id and m.user_id = auth.uid()
    )
  );

create policy message_visitor_feedback_delete_own
  on public.message_visitor_feedback
  for delete
  to authenticated
  using (
    exists (
      select 1 from public.messages m
      where m.id = message_id and m.user_id = auth.uid()
    )
  );

create policy agent_feedback_summary_select_own
  on public.agent_feedback_summary
  for select
  to authenticated
  using (user_id = auth.uid());

create policy agent_feedback_summary_insert_own
  on public.agent_feedback_summary
  for insert
  to authenticated
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy agent_feedback_summary_update_own
  on public.agent_feedback_summary
  for update
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy agent_feedback_summary_delete_own
  on public.agent_feedback_summary
  for delete
  to authenticated
  using (user_id = auth.uid());
