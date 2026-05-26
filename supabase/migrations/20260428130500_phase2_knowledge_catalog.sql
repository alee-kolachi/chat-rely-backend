-- Phase 2: Knowledge catalog and ingestion lifecycle

do $$
begin
  if not exists (select 1 from pg_type where typname = 'knowledge_source_type') then
    create type public.knowledge_source_type as enum (
      'website',
      'file',
      'text_snippet',
      'q_and_a'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'knowledge_source_status') then
    create type public.knowledge_source_status as enum (
      'pending',
      'indexing',
      'ready',
      'failed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'indexing_job_status') then
    create type public.indexing_job_status as enum (
      'queued',
      'running',
      'succeeded',
      'failed',
      'cancelled'
    );
  end if;
end
$$;

create table if not exists public.knowledge_sources (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  type public.knowledge_source_type not null,
  title text not null,
  status public.knowledge_source_status not null default 'pending',
  source_url text,
  storage_bucket text,
  storage_path text,
  raw_text text,
  checksum_sha256 text,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  error_message text,
  last_indexed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (type = 'website' and source_url is not null and source_url <> '')
    or (type = 'file' and storage_bucket is not null and storage_path is not null)
    or (type = 'text_snippet' and raw_text is not null and raw_text <> '')
    or (type = 'q_and_a')
  )
);

create index if not exists knowledge_sources_agent_id_idx
  on public.knowledge_sources(agent_id);

create index if not exists knowledge_sources_user_id_idx
  on public.knowledge_sources(user_id);

create index if not exists knowledge_sources_status_idx
  on public.knowledge_sources(status);

create index if not exists knowledge_sources_type_idx
  on public.knowledge_sources(type);

create table if not exists public.knowledge_qa_items (
  id uuid primary key default gen_random_uuid(),
  knowledge_source_id uuid not null references public.knowledge_sources(id) on delete cascade,
  question text not null,
  answer text not null,
  question_variants text[] not null default '{}',
  usage_count integer not null default 0 check (usage_count >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists knowledge_qa_items_source_idx
  on public.knowledge_qa_items(knowledge_source_id);

create table if not exists public.indexing_jobs (
  id uuid primary key default gen_random_uuid(),
  knowledge_source_id uuid not null references public.knowledge_sources(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status public.indexing_job_status not null default 'queued',
  attempt integer not null default 1 check (attempt >= 1),
  triggered_by text not null default 'system',
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (finished_at is null or started_at is null or finished_at >= started_at)
);

create index if not exists indexing_jobs_source_idx
  on public.indexing_jobs(knowledge_source_id, created_at desc);

create index if not exists indexing_jobs_agent_status_idx
  on public.indexing_jobs(agent_id, status, created_at desc);

create trigger set_knowledge_sources_updated_at
before update on public.knowledge_sources
for each row execute function public.set_updated_at();

create trigger set_knowledge_qa_items_updated_at
before update on public.knowledge_qa_items
for each row execute function public.set_updated_at();

create trigger set_indexing_jobs_updated_at
before update on public.indexing_jobs
for each row execute function public.set_updated_at();
