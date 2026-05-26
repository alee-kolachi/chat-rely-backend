-- Dashboard / analytics read-path indexes.
-- These reduce scan and sort costs for high-frequency summary endpoints.

create index if not exists conversations_user_agent_started_idx
  on public.conversations (user_id, agent_id, started_at desc);

create index if not exists conversations_user_agent_last_activity_idx
  on public.conversations (user_id, agent_id, last_activity_at desc);

create index if not exists conversations_user_agent_status_started_idx
  on public.conversations (user_id, agent_id, status, started_at desc);

create index if not exists messages_conversation_user_created_desc_idx
  on public.messages (conversation_id, user_id, created_at desc);

create index if not exists messages_conversation_role_created_desc_idx
  on public.messages (conversation_id, role, created_at desc);

create index if not exists tickets_user_agent_status_idx
  on public.tickets (user_id, agent_id, status);
