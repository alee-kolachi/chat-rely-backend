from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domains.plans.schemas import PublicPlanDTO


@pytest.fixture
def plans_public_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route uses Depends(get_db) → get_db_session; monkeypatching get_db is ineffective. Mock the service instead."""

    async def mock_list(_db: Any) -> list[PublicPlanDTO]:
        return [
            PublicPlanDTO(
                slug="free",
                name="Free",
                monthly_price_cents=0,
                included_conversations=50,
                overage_conversation_cents=0,
                max_agents=1,
                features={"pricing_card_bullets": ["50 conversations"]},
                throttle_policy={},
                sort_order=10,
            )
        ]

    monkeypatch.setattr("app.api.routes.plans.list_public_pricing_plans", mock_list)


def test_plans_public_route(client: TestClient, plans_public_patch: None) -> None:
    res = client.get("/api/v1/plans/public")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["slug"] == "free"
    assert body[0]["included_conversations"] == 50
    assert body[0]["limits"]["max_agents"] == 1
    assert body[0]["limits"]["included_conversations"] == 50
    assert body[0]["limits"]["max_enabled_actions_per_agent"] == 0
