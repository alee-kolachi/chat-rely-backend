# Reliable Chatbot Rules (MVP)

This file freezes the reliability and safety rules that database design, RAG behavior,
and action execution must follow.

## Product and tenancy

- Single-tenant per account for MVP.
- Multiple agents per user are allowed and constrained by subscription limits.
- No workspace/member permission model in this phase.

## Response reliability

- Strict grounded mode is required.
- Assistant answers must be based on:
  - Retrieved knowledge chunks tied to the current agent.
  - Validated external action results (for example Shopify API responses).
- If retrieval quality is below threshold, assistant must avoid guessing and either:
  - Ask a clarifying question, or
  - Use a fallback message indicating missing certainty.

## Hallucination and confidence policy

- Retrieval confidence is enforced using a minimum similarity threshold.
- Responses with weak evidence should not fabricate facts, links, policies, prices, or order details.
- Source metadata and action audit records must be persisted to support debugging and answer revision.

## Shopify action safety

- Read actions can execute automatically.
- Mutation actions (refund/update/cancel/edit) are permitted only when policy thresholds pass.
- Policy thresholds are stored in per-action safety configuration.
- Every mutation decision must be auditable with:
  - Requested action and inputs.
  - Policy evaluation outcome.
  - Action result and error class when relevant.

## Usage and billing behavior

- Billing unit is conversation-based at monthly period boundaries.
- A conversation is considered a single session bounded by inactivity timeout rules.
- Plan overage must not hard-stop chatbot availability for end users.
- Over-limit behavior uses progressive throttling tiers.
- Admin-facing transparency is required:
  - Usage percentage.
  - Projected usage.
  - Running overage estimate.

## Data handling boundaries

- Auth and sessions: Supabase Auth.
- Relational app state: Postgres.
- RAG vectors: pgvector in Postgres.
- Uploaded files: Supabase Storage with metadata in Postgres.
- Integration credentials: encrypted at rest; no plaintext tokens in app-facing tables.
