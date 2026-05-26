#!/usr/bin/env python3
"""
Create or update the ChatRely marketing-site agent and seed product knowledge.

Requires an existing user (sign up first). Use a plan with human escalation enabled
(Hobby or above; Free has max_enabled_actions_per_agent=0).

Usage:
  cd backend
  uv run python scripts/seed_platform_site_agent.py --email you@example.com
  uv run python scripts/seed_platform_site_agent.py --user-id <uuid> --crawl-site-url https://app.example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text

from app.db.engine import init_engine
from app.db.session import get_session_factory, init_session_factory
from app.domains.actions.schemas import AgentActionPatchRequest
from app.domains.actions.service import HUMAN_ACTION_KEY, patch_agent_action
from app.domains.agents.schemas import AgentCreateRequest
from app.domains.agents.service import create_agent
from app.domains.knowledge.schemas import WebsiteCrawlRequest
from app.domains.knowledge.service import (
    create_and_enqueue_dashboard_website,
    create_and_index_qa_pair,
    create_and_index_text_snippet,
)
from app.domains.platform_site.knowledge_content import (
    PLATFORM_SITE_QA,
    PLATFORM_SITE_SNIPPETS,
    PLATFORM_SITE_SYSTEM_PROMPT,
)

AGENT_NAME = "ChatRely Website"
AGENT_SLUG = "chatrely-website"


async def _resolve_user_id(session, *, email: str | None, user_id: str | None) -> UUID:
    if user_id:
        return UUID(user_id)
    if not email:
        raise SystemExit("Provide --email or --user-id")
    result = await session.execute(
        text("select id from auth.users where lower(email) = lower(:email) limit 1"),
        {"email": email.strip()},
    )
    row = result.mappings().first()
    if not row:
        raise SystemExit(f"No auth.users row for email: {email}")
    return UUID(str(row["id"]))


async def _find_agent(session, user_id: UUID) -> dict | None:
    result = await session.execute(
        text(
            """
            select id, public_key, system_prompt, behavior_settings
            from public.agents
            where user_id = cast(:user_id as uuid) and slug = :slug and archived_at is null
            limit 1
            """
        ),
        {"user_id": str(user_id), "slug": AGENT_SLUG},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _ensure_agent(session, user_id: UUID) -> dict:
    existing = await _find_agent(session, user_id)
    if existing:
        return existing
    dto = await create_agent(
        session,
        user_id,
        AgentCreateRequest(name=AGENT_NAME, slug=AGENT_SLUG, system_prompt=PLATFORM_SITE_SYSTEM_PROMPT),
    )
    return {
        "id": dto.id,
        "public_key": dto.public_key,
        "system_prompt": dto.system_prompt,
        "behavior_settings": dto.behavior_settings,
    }


async def _configure_agent(session, user_id: UUID, agent_id: UUID, behavior: dict) -> None:
    merged = dict(behavior or {})
    merged["agent_type"] = "custom"
    merged.setdefault("greeting_message", "Hi! Ask me about ChatRely plans, setup, or the embeddable widget.")
    merged.setdefault("brand_color", "#6366F1")
    merged.setdefault("widget_position", "bottom_right")
    await session.execute(
        text(
            """
            update public.agents
            set system_prompt = :system_prompt,
                behavior_settings = cast(:behavior_settings as jsonb),
                updated_at = now()
            where id = cast(:agent_id as uuid) and user_id = cast(:user_id as uuid)
            """
        ),
        {
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "system_prompt": PLATFORM_SITE_SYSTEM_PROMPT,
            "behavior_settings": json.dumps(merged),
        },
    )
    await session.commit()


async def _enable_human_escalation(session, user_id: UUID, agent_id: UUID) -> None:
    await patch_agent_action(
        session,
        user_id=user_id,
        agent_id=agent_id,
        action_key=HUMAN_ACTION_KEY,
        payload=AgentActionPatchRequest(
            enabled=True,
            config={
                "availability_mode": "manual_only",
                "estimated_response_minutes": 240,
                "support_email": "support@chatrely.com",
            },
        ),
    )


async def _qa_exists(session, agent_id: UUID, question: str) -> bool:
    result = await session.execute(
        text(
            """
            select 1
            from public.knowledge_sources ks
            join public.knowledge_qa_items qi on qi.knowledge_source_id = ks.id
            where ks.agent_id = cast(:agent_id as uuid)
              and ks.type = 'q_and_a'
              and lower(trim(qi.question)) = lower(trim(:question))
            limit 1
            """
        ),
        {"agent_id": str(agent_id), "question": question},
    )
    return result.first() is not None


async def _snippet_exists(session, agent_id: UUID, title: str) -> bool:
    result = await session.execute(
        text(
            """
            select 1
            from public.knowledge_sources
            where agent_id = cast(:agent_id as uuid)
              and type = 'text_snippet'
              and lower(trim(title)) = lower(trim(:title))
            limit 1
            """
        ),
        {"agent_id": str(agent_id), "title": title},
    )
    return result.first() is not None


async def _seed_knowledge(session, user_id: UUID, agent_id: UUID) -> None:
    for pair in PLATFORM_SITE_QA:
        if await _qa_exists(session, agent_id, pair.question):
            print(f"  skip Q&A: {pair.question[:60]}…")
            continue
        await create_and_index_qa_pair(
            session, user_id=user_id, agent_id=agent_id, question=pair.question, answer=pair.answer
        )
        await session.commit()
        print(f"  indexed Q&A: {pair.question[:60]}…")

    for snippet in PLATFORM_SITE_SNIPPETS:
        if await _snippet_exists(session, agent_id, snippet.title):
            print(f"  skip snippet: {snippet.title}")
            continue
        await create_and_index_text_snippet(
            session,
            user_id=user_id,
            agent_id=agent_id,
            title=snippet.title,
            snippet_text=snippet.body,
        )
        await session.commit()
        print(f"  indexed snippet: {snippet.title}")


def _crawl_request(agent_id: UUID, site_url: str) -> WebsiteCrawlRequest:
    parsed = urlparse(site_url.strip())
    if not parsed.netloc and not parsed.path:
        raise ValueError(f"Invalid site URL: {site_url}")
    protocol: str = "https://"
    if parsed.scheme == "http":
        protocol = "http://"
    host = parsed.netloc or parsed.path.split("/")[0]
    path = parsed.path if parsed.netloc else ("/".join(parsed.path.split("/")[1:]) if "/" in parsed.path else "")
    url_input = host + path
    return WebsiteCrawlRequest(agent_id=agent_id, protocol=protocol, url_input=url_input, title="ChatRely marketing site")


async def _maybe_crawl(session, user_id: UUID, agent_id: UUID, site_url: str | None) -> None:
    if not site_url:
        return
    print(f"Enqueueing website crawl for {site_url}…")
    payload = _crawl_request(agent_id, site_url)
    await create_and_enqueue_dashboard_website(session, user_id, payload, mode="crawl")
    await session.commit()
    print("Website source queued — run the indexing worker to complete the crawl.")


async def run(
    *,
    email: str | None,
    user_id: str | None,
    crawl_site_url: str | None,
    skip_knowledge: bool,
    skip_escalation: bool,
) -> int:
    public_key = ""
    factory = get_session_factory()
    async with factory() as session:
        uid = await _resolve_user_id(session, email=email, user_id=user_id)
        print(f"User: {uid}")
        agent = await _ensure_agent(session, uid)
        agent_id = UUID(str(agent["id"]))
        public_key = str(agent["public_key"])
        print(f"Agent: {agent_id}  public_key={public_key}")

        await _configure_agent(session, uid, agent_id, agent.get("behavior_settings") or {})

        if not skip_escalation:
            try:
                await _enable_human_escalation(session, uid, agent_id)
                print("Human escalation enabled (human.escalate).")
            except Exception as exc:
                print(f"Warning: could not enable human escalation: {exc}", file=sys.stderr)
                print("Use Hobby plan or above, then re-run.", file=sys.stderr)

        if not skip_knowledge:
            print("Seeding knowledge…")
            await _seed_knowledge(session, uid, agent_id)

        await _maybe_crawl(session, uid, agent_id, crawl_site_url)

    print()
    print("Set in app/.env.local:")
    print(f"NEXT_PUBLIC_CHATRELY_SITE_AGENT_KEY={public_key}")
    print("NEXT_PUBLIC_BACKEND_URL=<your public API URL>")
    return 0


def main() -> None:
    init_engine()
    init_session_factory()
    parser = argparse.ArgumentParser(description="Seed ChatRely marketing-site agent and knowledge.")
    parser.add_argument("--email", help="auth.users email for the platform workspace")
    parser.add_argument("--user-id", help="auth.users UUID")
    parser.add_argument(
        "--crawl-site-url",
        help="Public app origin to crawl (e.g. https://app.chatrely.com). Requires indexing worker.",
    )
    parser.add_argument("--skip-knowledge", action="store_true")
    parser.add_argument("--skip-escalation", action="store_true")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                email=args.email,
                user_id=args.user_id,
                crawl_site_url=args.crawl_site_url,
                skip_knowledge=args.skip_knowledge,
                skip_escalation=args.skip_escalation,
            )
        )
    )


if __name__ == "__main__":
    main()
