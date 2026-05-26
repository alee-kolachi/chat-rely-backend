-- Phase 7: Onboarding foundation, crawl observability, and truthful progress

do $$
begin
  if not exists (select 1 from pg_type where typname = 'onboarding_session_status') then
    create type public.onboarding_session_status as enum (
      'in_progress',
      'completed',
      'abandoned'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'onboarding_step_event_type') then
    create type public.onboarding_step_event_type as enum (
      'entered',
      'completed',
      'skipped',
      'failed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'crawl_run_status') then
    create type public.crawl_run_status as enum (
      'queued',
      'running',
      'succeeded',
      'failed',
      'cancelled'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'crawl_page_status') then
    create type public.crawl_page_status as enum (
      'queued',
      'fetched',
      'parsed',
      'failed',
      'excluded'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'indexing_job_phase') then
    create type public.indexing_job_phase as enum (
      'queued',
      'crawling',
      'chunking',
      'embedding',
      'persisting',
      'complete',
      'failed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'onboarding_checklist_status') then
    create type public.onboarding_checklist_status as enum (
      'todo',
      'in_progress',
      'done',
      'blocked'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'preview_asset_type') then
    create type public.preview_asset_type as enum (
      'screenshot',
      'preview_render',
      'thumbnail'
    );
  end if;
end
$$;

create table if not exists public.onboarding_sessions (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null unique references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status public.onboarding_session_status not null default 'in_progress',
  current_step integer not null default 1 check (current_step >= 1 and current_step <= 6),
  progress_pct integer not null default 0 check (progress_pct >= 0 and progress_pct <= 100),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (completed_at is null or completed_at >= started_at)
);

create index if not exists onboarding_sessions_user_status_updated_idx
  on public.onboarding_sessions(user_id, status, updated_at desc);

create table if not exists public.onboarding_step_events (
  id uuid primary key default gen_random_uuid(),
  onboarding_session_id uuid not null references public.onboarding_sessions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  step_key text not null,
  event_type public.onboarding_step_event_type not null,
  payload jsonb not null default '{}'::jsonb check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists onboarding_step_events_session_created_idx
  on public.onboarding_step_events(onboarding_session_id, created_at desc);

create table if not exists public.knowledge_crawl_runs (
  id uuid primary key default gen_random_uuid(),
  knowledge_source_id uuid not null references public.knowledge_sources(id) on delete cascade,
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  status public.crawl_run_status not null default 'queued',
  settings jsonb not null default '{}'::jsonb check (jsonb_typeof(settings) = 'object'),
  pages_discovered integer not null default 0 check (pages_discovered >= 0),
  pages_crawled integer not null default 0 check (pages_crawled >= 0),
  pages_failed integer not null default 0 check (pages_failed >= 0),
  links_discovered integer not null default 0 check (links_discovered >= 0),
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (finished_at is null or started_at is null or finished_at >= started_at)
);

create index if not exists knowledge_crawl_runs_source_status_idx
  on public.knowledge_crawl_runs(knowledge_source_id, status, created_at desc);

create table if not exists public.knowledge_source_pages (
  id uuid primary key default gen_random_uuid(),
  knowledge_source_id uuid not null references public.knowledge_sources(id) on delete cascade,
  crawl_run_id uuid references public.knowledge_crawl_runs(id) on delete set null,
  user_id uuid not null references auth.users(id) on delete cascade,
  url text not null check (url <> ''),
  canonical_url text,
  path text,
  depth integer not null default 0 check (depth >= 0),
  status public.crawl_page_status not null default 'queued',
  http_status integer check (http_status is null or http_status >= 100),
  content_hash text,
  error_message text,
  last_crawled_at timestamptz,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (knowledge_source_id, url)
);

create index if not exists knowledge_source_pages_run_status_idx
  on public.knowledge_source_pages(crawl_run_id, status);

create index if not exists knowledge_source_pages_source_status_updated_idx
  on public.knowledge_source_pages(knowledge_source_id, status, updated_at desc);

alter table public.indexing_jobs
  add column if not exists phase public.indexing_job_phase not null default 'queued',
  add column if not exists pages_total integer not null default 0 check (pages_total >= 0),
  add column if not exists pages_processed integer not null default 0 check (pages_processed >= 0),
  add column if not exists chunks_total integer not null default 0 check (chunks_total >= 0),
  add column if not exists chunks_embedded integer not null default 0 check (chunks_embedded >= 0),
  add column if not exists progress_pct integer not null default 0 check (progress_pct >= 0 and progress_pct <= 100);

create index if not exists indexing_jobs_running_idx
  on public.indexing_jobs(status, created_at desc)
  where status in ('queued', 'running');

create table if not exists public.onboarding_preview_assets (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  step_key text not null default 'step_2',
  asset_type public.preview_asset_type not null default 'screenshot',
  source_url text,
  storage_bucket text,
  storage_path text,
  mime_type text,
  byte_size integer check (byte_size is null or byte_size >= 0),
  width integer check (width is null or width >= 0),
  height integer check (height is null or height >= 0),
  capture_status text not null default 'pending',
  captured_at timestamptz,
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (storage_bucket, storage_path)
);

create index if not exists onboarding_preview_assets_agent_step_created_idx
  on public.onboarding_preview_assets(agent_id, step_key, created_at desc);

create table if not exists public.onboarding_checklist_items (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  item_key text not null,
  required boolean not null default true,
  status public.onboarding_checklist_status not null default 'todo',
  completed_at timestamptz,
  evidence jsonb not null default '{}'::jsonb check (jsonb_typeof(evidence) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (agent_id, item_key)
);

create index if not exists onboarding_checklist_items_agent_status_idx
  on public.onboarding_checklist_items(agent_id, status);

create trigger set_onboarding_sessions_updated_at
before update on public.onboarding_sessions
for each row execute function public.set_updated_at();

create trigger set_knowledge_crawl_runs_updated_at
before update on public.knowledge_crawl_runs
for each row execute function public.set_updated_at();

create trigger set_knowledge_source_pages_updated_at
before update on public.knowledge_source_pages
for each row execute function public.set_updated_at();

create trigger set_onboarding_preview_assets_updated_at
before update on public.onboarding_preview_assets
for each row execute function public.set_updated_at();

create trigger set_onboarding_checklist_items_updated_at
before update on public.onboarding_checklist_items
for each row execute function public.set_updated_at();
