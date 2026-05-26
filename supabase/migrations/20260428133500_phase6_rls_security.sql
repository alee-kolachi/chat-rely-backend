-- Phase 6: RLS baseline and RPC execution scope

create or replace function public.is_owner_of_agent(p_agent_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.agents a
    where a.id = p_agent_id
      and a.user_id = auth.uid()
  );
$$;

revoke all on function public.match_knowledge_chunks(uuid, vector, integer, double precision) from public;
grant execute on function public.match_knowledge_chunks(uuid, vector, integer, double precision) to authenticated;
grant execute on function public.match_knowledge_chunks(uuid, vector, integer, double precision) to service_role;

alter table public.profiles enable row level security;
alter table public.subscriptions enable row level security;
alter table public.agents enable row level security;
alter table public.shopify_connections enable row level security;
alter table public.agent_actions enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.usage_period_snapshots enable row level security;
alter table public.knowledge_sources enable row level security;
alter table public.knowledge_qa_items enable row level security;
alter table public.indexing_jobs enable row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.agent_reliability_settings enable row level security;
alter table public.action_execution_audits enable row level security;

-- Plans are globally readable to authenticated users.
alter table public.plans enable row level security;

create policy plans_select_active
  on public.plans
  for select
  to authenticated
  using (is_active = true);

create policy profiles_select_own
  on public.profiles
  for select
  to authenticated
  using (id = auth.uid());

create policy profiles_insert_own
  on public.profiles
  for insert
  to authenticated
  with check (id = auth.uid());

create policy profiles_update_own
  on public.profiles
  for update
  to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

create policy subscriptions_select_own
  on public.subscriptions
  for select
  to authenticated
  using (user_id = auth.uid());

create policy agents_select_own
  on public.agents
  for select
  to authenticated
  using (user_id = auth.uid());

create policy agents_insert_own
  on public.agents
  for insert
  to authenticated
  with check (user_id = auth.uid());

create policy agents_update_own
  on public.agents
  for update
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy agents_delete_own
  on public.agents
  for delete
  to authenticated
  using (user_id = auth.uid());

create policy shopify_connections_manage_own_agent
  on public.shopify_connections
  for all
  to authenticated
  using (public.is_owner_of_agent(agent_id))
  with check (public.is_owner_of_agent(agent_id));

create policy agent_actions_manage_own_agent
  on public.agent_actions
  for all
  to authenticated
  using (public.is_owner_of_agent(agent_id))
  with check (public.is_owner_of_agent(agent_id));

create policy conversations_manage_own
  on public.conversations
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy messages_manage_own_conversation
  on public.messages
  for all
  to authenticated
  using (
    user_id = auth.uid()
    and exists (
      select 1
      from public.conversations c
      where c.id = conversation_id
        and c.user_id = auth.uid()
    )
  )
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

create policy usage_period_snapshots_select_own
  on public.usage_period_snapshots
  for select
  to authenticated
  using (user_id = auth.uid());

create policy knowledge_sources_manage_own
  on public.knowledge_sources
  for all
  to authenticated
  using (user_id = auth.uid() and public.is_owner_of_agent(agent_id))
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy knowledge_qa_items_manage_own
  on public.knowledge_qa_items
  for all
  to authenticated
  using (
    exists (
      select 1
      from public.knowledge_sources s
      where s.id = knowledge_source_id
        and s.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1
      from public.knowledge_sources s
      where s.id = knowledge_source_id
        and s.user_id = auth.uid()
    )
  );

create policy indexing_jobs_select_own
  on public.indexing_jobs
  for select
  to authenticated
  using (user_id = auth.uid());

create policy knowledge_chunks_manage_own
  on public.knowledge_chunks
  for all
  to authenticated
  using (user_id = auth.uid() and public.is_owner_of_agent(agent_id))
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy agent_reliability_settings_manage_own
  on public.agent_reliability_settings
  for all
  to authenticated
  using (user_id = auth.uid() and public.is_owner_of_agent(agent_id))
  with check (user_id = auth.uid() and public.is_owner_of_agent(agent_id));

create policy action_execution_audits_select_own
  on public.action_execution_audits
  for select
  to authenticated
  using (user_id = auth.uid());
