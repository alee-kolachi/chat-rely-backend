from fastapi import APIRouter

from app.api.routes.admin import admin_router
from app.api.routes.agent_actions import router as agent_actions_router
from app.api.routes.agents import router as agents_router
from app.api.routes.billing import router as billing_router
from app.api.routes.bootstrap import router as bootstrap_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.health import router as health_router
from app.api.routes.integrations_shopify import router as integrations_shopify_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.knowledge_files import router as knowledge_files_router
from app.api.routes.knowledge_qa import router as knowledge_qa_router
from app.api.routes.knowledge_snippets import router as knowledge_snippets_router
from app.api.routes.knowledge_website import router as knowledge_website_router
from app.api.routes.mailjet_inbound import router as mailjet_inbound_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.plans import router as plans_router
from app.api.routes.public_widget import router as public_widget_router
from app.api.routes.profile import router as profile_router
from app.api.routes.runtime import router as runtime_router
from app.api.routes.system import router as system_router
from app.api.routes.tickets import router as tickets_router
from app.api.routes.webhooks_stripe import router as webhooks_stripe_router


def get_api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(health_router)
    router.include_router(system_router)
    router.include_router(bootstrap_router)
    router.include_router(plans_router)
    router.include_router(public_widget_router)
    router.include_router(profile_router)
    router.include_router(notifications_router)
    router.include_router(agents_router)
    router.include_router(agent_actions_router)
    # Register website routes before generic `/knowledge/*` so nested paths always resolve.
    router.include_router(knowledge_website_router)
    router.include_router(knowledge_files_router)
    router.include_router(knowledge_snippets_router)
    router.include_router(knowledge_qa_router)
    router.include_router(knowledge_router)
    router.include_router(onboarding_router)
    router.include_router(integrations_shopify_router)
    router.include_router(runtime_router)
    router.include_router(conversations_router)
    router.include_router(tickets_router)
    router.include_router(mailjet_inbound_router)
    router.include_router(billing_router)
    router.include_router(webhooks_stripe_router)
    router.include_router(admin_router)
    return router

