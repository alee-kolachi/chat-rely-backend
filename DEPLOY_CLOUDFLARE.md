# Deploy backend on Cloudflare

This API runs on **Cloudflare Containers** (Docker + Worker gateway). It is not a pure Python Worker: LangChain, SQLAlchemy, asyncpg, and background workers need a full Linux container.

## Prerequisites

- [Cloudflare account](https://dash.cloudflare.com/) with **Workers Paid** (Containers billing)
- [Docker Desktop](https://docs.docker.com/desktop/) running locally
- Node.js 20+
- `uv` for local API development (optional)

## One-time setup

```bash
cd chat-rely-backend
npm install
cp .dev.vars.example .dev.vars   # local wrangler dev only
```

Set production secrets (repeat for each key you use from `.env.example`):

```bash
node scripts/print-wrangler-secrets.mjs
# then, for example:
npx wrangler secret put DATABASE_URL
npx wrangler secret put SUPABASE_JWKS_URL
# ...
```

Also set non-secret config in `wrangler.jsonc` under `"vars"` or via dashboard.

**Required secrets for a working API:**

- `DATABASE_URL` — Supabase pooler (`postgresql+asyncpg://...:6543/postgres`)
- `SUPABASE_JWKS_URL`, `SUPABASE_ISSUER`, `SUPABASE_AUDIENCE`
- `OPENAI_API_KEY`
- `PUBLIC_API_BASE_URL` — your Worker URL after first deploy, e.g. `https://chat-rely-backend.<account>.workers.dev`
- `BILLING_APP_BASE_URL` — frontend URL, e.g. `https://app.example.com`
- `ALLOWED_ORIGINS` — JSON array, e.g. `["https://app.example.com"]`
- `INTEGRATION_TOKEN_FERNET_KEY`, `INTEGRATION_OAUTH_STATE_SECRET`
- Stripe / Shopify keys if you use those features

After first deploy, update `PUBLIC_API_BASE_URL` to the real Worker hostname and redeploy.

## Deploy

```bash
npm run cf:deploy
```

First deploy builds the Docker image (several minutes). The Worker URL becomes your public API base.

## Local dev with Cloudflare

```bash
npm run cf:dev
```

Uses `.dev.vars` and runs the container via Wrangler (Docker required).

For everyday Python work without Docker:

```bash
uv sync
uv run uvicorn app.main:create_app --factory --reload
```

## Background workers

Indexing and maintenance workers are **separate processes**. They are not started by the Container image. Options:

1. Run workers on another host (Railway, Fly, cron VM) with the same `DATABASE_URL` and env.
2. Add a second Cloudflare Container + cron Worker later (not included in this repo).

## GitHub → Cloudflare

In **Workers & Pages → your worker → Settings → Builds**:

- Build command: `npm install && npm run cf:deploy`
- Or use GitHub Actions with `CLOUDFLARE_API_TOKEN` and `wrangler deploy`

## Health check

- Liveness: `GET /api/v1/health/live`
- Readiness: `GET /api/v1/health/ready`
