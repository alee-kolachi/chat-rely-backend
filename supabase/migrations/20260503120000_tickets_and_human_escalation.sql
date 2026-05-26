-- Tickets for human escalations; human_escalation_enabled on free plan (default true when merged).

update public.plans
set
  features = coalesce(features, '{}'::jsonb) || '{"human_escalation_enabled": true}'::jsonb,
  updated_at = now()
where slug = 'free';

create table if not exists public.tickets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  status text not null default 'open' check (status in ('open', 'pending_customer', 'resolved')),
  subject text,
  priority text not null default 'medium' check (priority in ('low', 'medium', 'high')),
  customer_email text,
  external_provider text,
  external_id text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (conversation_id)
);

create index if not exists tickets_user_id_updated_at_idx
  on public.tickets (user_id, updated_at desc);

create index if not exists tickets_agent_id_updated_at_idx
  on public.tickets (agent_id, updated_at desc);

alter table public.tickets enable row level security;

create policy tickets_manage_own
  on public.tickets
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and public.is_owner_of_agent(agent_id)
  );
