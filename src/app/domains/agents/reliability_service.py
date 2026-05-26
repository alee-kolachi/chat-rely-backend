from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.domains.agents.reliability_schemas import (
    AgentReliabilityDTO,
    AgentReliabilityUpdateRequest,
)


async def _ensure_agent_owned(db: AsyncSession, user_id: UUID, agent_id: UUID) -> None:
    row = (
        await db.execute(
            text(
                """
                select 1
                from public.agents
                where id = :agent_id and user_id = :user_id
                """
            ),
            {"agent_id": str(agent_id), "user_id": str(user_id)},
        )
    ).first()
    if row is None:
        raise AppError(code="agent.not_found", message="Agent not found", status_code=404)


async def _fetch_reliability(db: AsyncSession, user_id: UUID, agent_id: UUID) -> AgentReliabilityDTO:
    row = (
        await db.execute(
            text(
                """
                select
                  agent_id, user_id, min_retrieval_similarity, inactivity_timeout_minutes,
                  max_unresolved_turns_before_escalation, fallback_message, created_at, updated_at
                from public.agent_reliability_settings
                where agent_id = :agent_id and user_id = :user_id
                """
            ),
            {"agent_id": str(agent_id), "user_id": str(user_id)},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            code="agent.reliability_not_found",
            message="Reliability settings not found",
            status_code=404,
        )
    return AgentReliabilityDTO.model_validate(row)


async def get_reliability(db: AsyncSession, user_id: UUID, agent_id: UUID) -> AgentReliabilityDTO:
    await _ensure_agent_owned(db, user_id, agent_id)
    # Defensive upsert: agent_reliability_settings is normally seeded on agent creation,
    # but make this endpoint resilient for any agents created before the seed was added.
    await db.execute(
        text(
            """
            insert into public.agent_reliability_settings (agent_id, user_id)
            values (:agent_id, :user_id)
            on conflict (agent_id) do nothing
            """
        ),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )
    await db.commit()
    return await _fetch_reliability(db, user_id, agent_id)


async def update_reliability(
    db: AsyncSession,
    user_id: UUID,
    agent_id: UUID,
    payload: AgentReliabilityUpdateRequest,
) -> AgentReliabilityDTO:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise AppError(
            code="validation.invalid_input",
            message="No fields provided for update",
            status_code=422,
        )

    await _ensure_agent_owned(db, user_id, agent_id)

    # Defensive upsert so PATCH works even if the row is missing.
    await db.execute(
        text(
            """
            insert into public.agent_reliability_settings (agent_id, user_id)
            values (:agent_id, :user_id)
            on conflict (agent_id) do nothing
            """
        ),
        {"agent_id": str(agent_id), "user_id": str(user_id)},
    )

    fields: list[str] = []
    params: dict[str, object] = {"agent_id": str(agent_id), "user_id": str(user_id)}
    for key, value in updates.items():
        fields.append(f"{key} = :{key}")
        params[key] = value
    fields.append("updated_at = now()")

    result = await db.execute(
        text(
            f"""
            update public.agent_reliability_settings
            set {", ".join(fields)}
            where agent_id = :agent_id and user_id = :user_id
            returning
              agent_id, user_id, min_retrieval_similarity, inactivity_timeout_minutes,
              max_unresolved_turns_before_escalation, fallback_message, created_at, updated_at
            """
        ),
        params,
    )
    row = result.mappings().first()
    if row is None:
        raise AppError(
            code="agent.reliability_not_found",
            message="Reliability settings not found",
            status_code=404,
        )

    await db.commit()
    return AgentReliabilityDTO.model_validate(row)
