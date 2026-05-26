# Schema Blueprint (v1)

This document maps the foundation schema to product requirements and migration files.

## Ownership model

- `auth.users` is the owner boundary for MVP.
- Each app table stores `user_id` directly or references an owned `agent_id`.
- RLS policies enforce `auth.uid()` ownership.

## Table contracts

### Identity and billing

- `profiles`: profile and notification preferences linked 1:1 with `auth.users`.
- `plans`: product limits and feature flags (`features`, `throttle_policy`).
- `subscriptions`: period/state/provider mappings for entitlement checks.
- `usage_period_snapshots`: per-period aggregate usage, overage, and throttle tier.

### Agent and integrations

- `agents`: per-user assistant identity and behavior settings.
- `shopify_connections`: encrypted integration tokens and scopes.
- `agent_actions`: enabled actions and per-action config/safety policy JSON.
- `agent_reliability_settings`: retrieval threshold, fallback, inactivity timeout.
- `action_execution_audits`: action policy decisions and execution telemetry.

### Conversation and runtime traces

- `conversations`: session boundary for billing and support timeline.
- `messages`: turn-level transcript, tool payloads, token usage, latency.

### Knowledge and indexing

- `knowledge_sources`: source metadata and indexing lifecycle state.
- `knowledge_qa_items`: structured Q&A overrides.
- `indexing_jobs`: queue/retry/progress/audit for ingestion workers.
- `knowledge_chunks`: vectorized chunks for retrieval.

## Vector retrieval design

- One vector table (`knowledge_chunks`) for all source types.
- Embeddings are currently declared as `vector(1536)`.
- RPC `match_knowledge_chunks(...)` returns top chunks above a threshold.
- Retrieval policy assumes strict grounding and fallback on weak matches.

## Constraint highlights

- Positive integer checks for all usage/limits/token counters.
- JSON payloads constrained to object shape where applicable.
- One open conversation per `(agent_id, visitor_id)`.
- One Shopify connection per agent for MVP.
- Unique per-agent action key.

## Index highlights

- Ownership and timeline indexes on `agents`, `conversations`, `messages`.
- Lifecycle indexes on `knowledge_sources` and `indexing_jobs`.
- HNSW index on `knowledge_chunks.embedding`.

## Migration mapping

- `20260428125500_phase1_core_schema.sql`: core relational foundation.
- `20260428130500_phase2_knowledge_catalog.sql`: knowledge catalog + indexing state.
- `20260428131500_phase3_vector_layer.sql`: vector table + retrieval RPC.
- `20260428132500_phase4_5_guardrails_usage.sql`: reliability/audit/usage helpers.
- `20260428133500_phase6_rls_security.sql`: RLS policies and RPC grants.
