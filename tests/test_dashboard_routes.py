"""Dashboard API and conversation list filters."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.domains.conversations.schemas import ConversationDTO
from app.domains.dashboard.schemas import (
    AgentDashboardResponse,
    DashboardRecentRow,
    DashboardSeriesPoint,
    TrainingTopicSummary,
)


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test"}


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        return {"sub": "00000000-0000-4000-8000-000000000001"}


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())


def test_get_agent_dashboard(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    agent_id = uuid4()
    now = datetime.now(tz=UTC)

    async def _dash(*_: Any, **__: Any) -> AgentDashboardResponse:
        return AgentDashboardResponse(
            range_from=now,
            range_to=now,
            conversations_started=3,
            active_conversations=1,
            resolved_by_agent_pct=50.0,
            needs_human_pct=10.0,
            open_escalations=1,
            awaiting_customer_reply=2,
            series=[DashboardSeriesPoint(bucket_date=now.date(), count=3)],
            recent=[
                DashboardRecentRow(
                    conversation_id=uuid4(),
                    visitor_id="v1",
                    topic_preview="Hi",
                    status="open",
                    last_activity_at=now,
                )
            ],
            training_topics=[
                TrainingTopicSummary(slug="returns", label="Returns policy", count=2),
            ],
            sources_suggestions_enabled=True,
        )

    monkeypatch.setattr("app.api.routes.agents.build_agent_dashboard", _dash)

    response = client.get(
        f"/api/v1/agents/{agent_id}/dashboard?range_key=7d",
        headers=_auth_header(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["conversations_started"] == 3
    assert body["active_conversations"] == 1
    assert body["resolved_by_agent_pct"] == 50.0
    assert body["training_topics"][0]["slug"] == "returns"
    assert body.get("sources_suggestions_enabled") is True


def test_list_conversations_training_topic_query(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_auth(monkeypatch)
    called: dict[str, Any] = {}

    async def _list(
        db: Any,
        user_id: Any,
        *,
        agent_id: Any = None,
        status: Any = None,
        started_after: Any = None,
        started_before: Any = None,
        training_topic_slug: Any = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationDTO]:
        called["training_topic_slug"] = training_topic_slug
        called["agent_id"] = agent_id
        return []

    monkeypatch.setattr("app.api.routes.conversations.list_conversations", _list)

    aid = uuid4()
    response = client.get(
        f"/api/v1/conversations?agent_id={aid}&training_topic=returns-policy",
        headers=_auth_header(),
    )
    assert response.status_code == 200
    assert called["training_topic_slug"] == "returns-policy"
    assert str(called["agent_id"]) == str(aid)
