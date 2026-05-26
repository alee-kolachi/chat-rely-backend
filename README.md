# ChatRely Backend

FastAPI platform foundation for ChatRely.
 
## Run

```bash
uv run uvicorn app.main:create_app --factory --reload
```

Website crawls run in a separate worker (API reload does not pick up crawl changes):

```bash
uv run python -m app.workers.indexing_worker
```

## Quality checks

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

## Marketing-site chatbot (dogfood widget)

1. Sign up a dedicated workspace (Hobby+ so human escalation can be enabled).
2. Seed agent + knowledge:

```bash
uv run python scripts/seed_platform_site_agent.py --email support+platform@example.com
uv run python scripts/seed_platform_site_agent.py --email … --crawl-site-url https://your-app.example.com
```

3. Run the indexing worker if you queued a website crawl.
4. Test in dashboard **Playground**, then set in `app/.env.local`:

```bash
NEXT_PUBLIC_CHATRELY_SITE_AGENT_KEY=<public_key from script output>
NEXT_PUBLIC_BACKEND_URL=<public API URL>
```

5. Build and host `widget.js` (see repo root README). The Next app loads the widget from the root layout when the env key is set.
