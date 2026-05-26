-- Phase 3: Vector store for RAG retrieval

create extension if not exists vector;

create table if not exists public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references public.agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  knowledge_source_id uuid not null references public.knowledge_sources(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null,
  embedding vector(1536) not null,
  token_count integer not null default 0 check (token_count >= 0),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (knowledge_source_id, chunk_index)
);

create index if not exists knowledge_chunks_agent_id_idx
  on public.knowledge_chunks(agent_id);

create index if not exists knowledge_chunks_source_id_idx
  on public.knowledge_chunks(knowledge_source_id);

create index if not exists knowledge_chunks_user_id_idx
  on public.knowledge_chunks(user_id);

create index if not exists knowledge_chunks_embedding_hnsw_idx
  on public.knowledge_chunks using hnsw (embedding vector_cosine_ops);

create or replace function public.match_knowledge_chunks(
  p_agent_id uuid,
  p_query_embedding vector(1536),
  p_match_count integer default 8,
  p_min_score double precision default 0.72
)
returns table (
  id uuid,
  knowledge_source_id uuid,
  content text,
  metadata jsonb,
  similarity double precision
)
language sql
stable
set search_path = public
as $$
  select
    c.id,
    c.knowledge_source_id,
    c.content,
    c.metadata,
    (1 - (c.embedding <=> p_query_embedding)) as similarity
  from public.knowledge_chunks c
  where c.agent_id = p_agent_id
    and (1 - (c.embedding <=> p_query_embedding)) >= p_min_score
  order by c.embedding <=> p_query_embedding
  limit greatest(p_match_count, 1);
$$;

comment on function public.match_knowledge_chunks(uuid, vector, integer, double precision)
  is 'Returns top matching chunks for one agent with a minimum similarity threshold. Use for grounded responses only.';
