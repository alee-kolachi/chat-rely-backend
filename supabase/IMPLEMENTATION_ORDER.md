# Implementation Order (DB + RAG)

This checklist is the execution sequence to avoid schema churn and reliability regressions.

## 1) Apply schema migrations

Run in timestamp order:

1. `20260428125500_phase1_core_schema.sql`
2. `20260428130500_phase2_knowledge_catalog.sql`
3. `20260428131500_phase3_vector_layer.sql`
4. `20260428132500_phase4_5_guardrails_usage.sql`
5. `20260428133500_phase6_rls_security.sql`
6. `seed.sql`

## 2) Back-end enforcement points

- Agent creation gate:
  - load active subscription + plan
  - enforce `max_agents`
- Feature gates:
  - check `plans.features` before enabling Shopify/actions/storage-heavy paths
- Response policy:
  - call `match_knowledge_chunks` with agent and threshold
  - fallback when result set is empty/weak
- Action policy:
  - evaluate `agent_actions.safety_policy`
  - write action decision/result to `action_execution_audits`

## 3) Conversation and usage lifecycle

- On new user turn:
  - locate open conversation for `(agent_id, visitor_id)`; else create
  - append `messages`, update message counters/tokens on `conversations`
- Periodic job:
  - call `close_idle_conversations()`
  - call `mark_conversation_billable(conversation_id)` for newly closed rows
  - call `refresh_usage_period_snapshot(user_id, period_start, period_end)`

## 4) Knowledge ingestion lifecycle

- Create `knowledge_sources` row with `pending`.
- Start `indexing_jobs` row -> `running`.
- Extract content, chunk, embed, write `knowledge_chunks`.
- Set source status `ready`, job `succeeded`.
- On failure: set source `failed`, persist `error_message`, set job `failed`.

## 5) Retrieval contract for strict grounding

- Query embedding must match table dimension (`1536`).
- Use `min_retrieval_similarity` from `agent_reliability_settings` if present.
- Never answer unsupported claims when matches are weak.
- Prefer clarify/fallback/escalate over speculative output.
