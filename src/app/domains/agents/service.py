import json
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.agents.schemas import AgentCreateRequest, AgentDTO, AgentUpdateRequest
from app.domains.bootstrap.service import _ensure_default_subscription
from app.domains.plans.plan_limits import plan_limits_dto_from_row


def _slugify(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return candidate or "agent"


async def _get_user_agent_count(db: AsyncSession, user_id: UUID) -> int:
    result = await db.execute(
        text(
            """
            select count(*) as total
            from public.agents
            where user_id = :user_id and status != 'archived'
            """
        ),
        {"user_id": str(user_id)},
    )
    return int(result.mappings().one()["total"])


async def _get_user_plan_max_agents(db: AsyncSession, user_id: UUID) -> int:
    _, plan = await _ensure_default_subscription(db, user_id)
    return plan_limits_dto_from_row(
        included_conversations=plan.included_conversations,
        max_agents=plan.max_agents,
        features=plan.features,
    ).max_agents


async def _fetch_agent_by_id(db: AsyncSession, user_id: UUID, agent_id: UUID) -> AgentDTO:
    result = await db.execute(
        text(
            """
            select
              id, user_id, name, slug, public_key, system_prompt, model, behavior_settings, status, created_at, updated_at, archived_at
            from public.agents
            where id = :agent_id and user_id = :user_id
            """
        ),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    if not row:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)
    return AgentDTO.model_validate(row)


async def create_agent(db: AsyncSession, user_id: UUID, payload: AgentCreateRequest) -> AgentDTO:
    current_count = await _get_user_agent_count(db, user_id)
    max_agents = await _get_user_plan_max_agents(db, user_id)
    if current_count >= max_agents:
        raise AppError(
            code="plan.limit_exceeded",
            message="Agent limit reached for current plan",
            status_code=409,
            details={"max_agents": max_agents, "current_agents": current_count},
        )

    slug = payload.slug or _slugify(payload.name)
    model = payload.model or "gpt-4o-mini"
    system_prompt = payload.system_prompt or ""
    behavior_settings = payload.behavior_settings or {}

    try:
        result = await db.execute(
            text(
                """
                insert into public.agents (
                  user_id, name, slug, model, system_prompt, behavior_settings
                ) values (
                  :user_id, :name, :slug, :model, :system_prompt, cast(:behavior_settings as jsonb)
                )
                returning
                  id, user_id, name, slug, public_key, system_prompt, model, behavior_settings, status, created_at, updated_at, archived_at
                """
            ),
            {
                "user_id": str(user_id),
                "name": payload.name,
                "slug": slug,
                "model": model,
                "system_prompt": system_prompt,
                "behavior_settings": json.dumps(behavior_settings),
            },
        )
        row = result.mappings().one()
    except IntegrityError as exc:
        await db.rollback()
        if "agents_user_id_slug_key" in str(exc):
            raise AppError(
                code="agent.slug_conflict",
                message="An agent with this slug already exists",
                status_code=409,
                details={"slug": slug},
            ) from exc
        raise

    await db.execute(
        text(
            """
            insert into public.agent_reliability_settings (agent_id, user_id)
            values (:agent_id, :user_id)
            on conflict (agent_id) do nothing
            """
        ),
        {"agent_id": str(row["id"]), "user_id": str(user_id)},
    )

    await db.commit()
    return AgentDTO.model_validate(row)


async def list_agents(db: AsyncSession, user_id: UUID, include_archived: bool = False) -> list[AgentDTO]:
    query = """
        select
          id, user_id, name, slug, public_key, system_prompt, model, behavior_settings, status, created_at, updated_at, archived_at
        from public.agents
        where user_id = :user_id
    """
    if not include_archived:
        query += " and status != 'archived'"
    query += " order by created_at desc"
    result = await db.execute(text(query), {"user_id": str(user_id)})
    rows = result.mappings().all()
    return [AgentDTO.model_validate(row) for row in rows]


async def update_agent(db: AsyncSession, user_id: UUID, agent_id: UUID, payload: AgentUpdateRequest) -> AgentDTO:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise AppError(code="validation.invalid_input", message="No fields provided for update", status_code=422)

    fields: list[str] = []
    params: dict[str, object] = {"agent_id": str(agent_id), "user_id": str(user_id)}
    for key, value in updates.items():
        fields.append(f"{key} = :{key}")
        if key == "behavior_settings":
            params[key] = json.dumps(value)
            fields[-1] = "behavior_settings = cast(:behavior_settings as jsonb)"
        else:
            params[key] = value
    fields.append("updated_at = now()")

    try:
        result = await db.execute(
            text(
                f"""
                update public.agents
                set {", ".join(fields)}
                where id = :agent_id and user_id = :user_id
                returning
                  id, user_id, name, slug, public_key, system_prompt, model, behavior_settings, status, created_at, updated_at, archived_at
                """
            ),
            params,
        )
        row = result.mappings().first()
    except IntegrityError as exc:
        await db.rollback()
        if "agents_user_id_slug_key" in str(exc):
            raise AppError(
                code="agent.slug_conflict",
                message="An agent with this slug already exists",
                status_code=409,
                details={"slug": str(params.get("slug")) if "slug" in params else None},
            ) from exc
        raise
    if row is None:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)

    await db.commit()
    return AgentDTO.model_validate(row)

