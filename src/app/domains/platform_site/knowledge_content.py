"""Curated Q&A and snippets for the ChatRely marketing-site agent.

Keep pricing facts aligned with ``app/lib/marketing/pricing-catalog.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformQAPair:
    question: str
    answer: str


@dataclass(frozen=True)
class PlatformSnippet:
    title: str
    body: str


PLATFORM_SITE_SYSTEM_PROMPT = """You are ChatRely's website assistant for prospective and current merchants learning about the product.

Answer only from retrieved knowledge and this conversation. Do not invent pricing, plan limits, features, or policies.

Scope: what ChatRely is, plans and pricing, signup and onboarding, the dashboard, widget embed, Shopify integration, and security or privacy at a high level.

Out of scope: other customers' data, legal advice, and store-specific orders or catalog (you cannot access any merchant's Shopify store).

When helpful, point visitors to /signup, /pricing, or support@chatrely.com. Be brief and direct."""

PLATFORM_SITE_QA: tuple[PlatformQAPair, ...] = (
    PlatformQAPair(
        question="What is ChatRely?",
        answer=(
            "ChatRely is an AI support assistant for Shopify merchants. You add knowledge about your store, "
            "optionally connect Shopify for live product and order data, test in the playground, and embed a "
            "chat widget on your storefront. Sign up at /signup to get started."
        ),
    ),
    PlatformQAPair(
        question="Who is ChatRely for?",
        answer=(
            "E-commerce brands on Shopify that want fast, accurate customer support without hiring a large team. "
            "It works for solo stores and growing teams that need policies, product questions, and order lookups in chat."
        ),
    ),
    PlatformQAPair(
        question="Do I need Shopify to use ChatRely?",
        answer=(
            "No. You can use knowledge-base content and the embeddable widget without Shopify. "
            "Connecting Shopify unlocks live catalog, inventory, and order tools for richer answers."
        ),
    ),
    PlatformQAPair(
        question="How do I embed the chat widget on my site?",
        answer=(
            "In the dashboard, open Deploy and copy the embed snippet. It loads widget.js with "
            "data-chatrely-agent-key (your public embed key) and data-chatrely-api-base (your public API URL). "
            "Paste the script before </body> on your storefront or theme."
        ),
    ),
    PlatformQAPair(
        question="How do I get started?",
        answer=(
            "1) Sign up at /signup. 2) Complete onboarding (agent name, optional website knowledge). "
            "3) Add knowledge (website, files, snippets, or Q&A). 4) Test in Playground. "
            "5) Copy the embed snippet from Deploy and add it to your store."
        ),
    ),
    PlatformQAPair(
        question="How much does the Standard plan cost?",
        answer=(
            "Standard is $99/month and includes 1,000 conversations per month, 2 agents, 5 AI actions per agent, "
            "40 MB training content, smart resolution for complex issues, and analytics. "
            "See /pricing for the full comparison."
        ),
    ),
    PlatformQAPair(
        question="What is the difference between Essential AI and smart resolution?",
        answer=(
            "Essential AI handles most chats on every plan and counts toward your monthly conversations. "
            "Standard and Pro can use smart resolution on harder questions within a monthly premium allowance. "
            "Chat stays on during busy periods; replies may slow slightly under heavy load."
        ),
    ),
    PlatformQAPair(
        question="Where is my data stored and who processes it?",
        answer=(
            "ChatRely hosts application data on our infrastructure and uses providers such as OpenAI for "
            "language models and embeddings. See /privacy for collection, use, subprocessors, and retention details."
        ),
    ),
    PlatformQAPair(
        question="Can you access my Shopify orders or account?",
        answer=(
            "This assistant only answers questions about the ChatRely product. It cannot access your store, "
            "dashboard account, or orders. Sign in to the dashboard or email support@chatrely.com for account help."
        ),
    ),
)

PLATFORM_SITE_SNIPPETS: tuple[PlatformSnippet, ...] = (
    PlatformSnippet(
        title="ChatRely pricing matrix",
        body="""ChatRely plan pricing (USD/month, marketing catalog):

Free — $0/mo — 30 conversations — 1 agent — 0 AI actions — 500 KB training — Essential AI — Shopify connect
Hobby — $29/mo — 250 conversations — 1 agent — 3 AI actions — 15 MB training — Essential AI — Shopify + actions
Standard — $99/mo — 1,000 conversations — 2 agents — 5 AI actions — 40 MB training — Smart resolution + analytics
Pro — $399/mo — 5,000 conversations — 5 agents — 8 AI actions — 100 MB training — Smart resolution, visitor feedback on widget, remove Powered by ChatRely branding

Overage display (marketing): Free $0.000, Hobby $0.145, Standard $0.099, Pro $0.080 per conversation beyond included.
Always-on chat may slow during heavy use on paid tiers.

Full matrix: /pricing""",
    ),
    PlatformSnippet(
        title="ChatRely feature glossary",
        body="""Essential AI: Default model handling for most visitor messages; included in monthly conversation allowance on all plans.

Smart resolution: Stronger model path for complex issues on Standard and Pro within a monthly premium turn allowance.

AI actions: Shopify-connected automations (product search, order lookup, etc.) capped per plan per agent.

Knowledge sources: Website crawl, file upload, text snippets, and Q&A pairs; indexed for retrieval at chat time.

Playground: Dashboard chat to test the agent before embedding the widget.

Widget: Embeddable script (widget.js) with a public agent key; streams replies via the public chat API.

Human escalation: Visitors can request a person; creates a ticket for the merchant team when enabled.""",
    ),
    PlatformSnippet(
        title="ChatRely setup checklist",
        body="""Merchant setup checklist:
1. Create account at /signup and name your agent.
2. Optional: add website knowledge during onboarding or in Knowledge → Website.
3. Add policies and FAQs via files, snippets, or Q&A.
4. Optional: connect Shopify in Actions for live store tools.
5. Tune appearance and tone under Agent settings.
6. Test in Playground.
7. Deploy: copy embed snippet to your theme (Deploy page).
8. Monitor conversations and analytics in the dashboard.""",
    ),
    PlatformSnippet(
        title="ChatRely support boundaries",
        body="""This website assistant explains ChatRely only. It cannot log into your dashboard, change billing, or read your Shopify data.

For account, billing, or bug reports: support@chatrely.com
For product signup: /signup
For plan details: /pricing""",
    ),
)
