from fastapi import APIRouter

from app.api.routes.admin.agents import router as agents_router
from app.api.routes.admin.billing import router as billing_router
from app.api.routes.admin.conversations import router as conversations_router
from app.api.routes.admin.costing import router as costing_router
from app.api.routes.admin.knowledge import router as knowledge_router
from app.api.routes.admin.me import router as me_router
from app.api.routes.admin.overview import router as overview_router
from app.api.routes.admin.plans import router as plans_router
from app.api.routes.admin.system import router as system_router
from app.api.routes.admin.tickets import router as tickets_router
from app.api.routes.admin.users import router as users_router

admin_router = APIRouter(prefix="/admin", tags=["admin"])
admin_router.include_router(me_router)
admin_router.include_router(overview_router)
admin_router.include_router(users_router)
admin_router.include_router(agents_router)
admin_router.include_router(conversations_router)
admin_router.include_router(tickets_router)
admin_router.include_router(knowledge_router)
admin_router.include_router(billing_router)
admin_router.include_router(plans_router)
admin_router.include_router(costing_router)
admin_router.include_router(system_router)
