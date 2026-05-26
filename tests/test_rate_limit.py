import pytest
from fastapi.testclient import TestClient

from app.core.errors import RateLimitError
from app.core.rate_limit import get_limiter
from app.core.settings import get_settings
from app.domains.agents.rate_limit import (
    enforce_visitor_message_rate_limit,
    parse_agent_rate_limit,
)
from app.main import create_app


@pytest.fixture(autouse=True)
async def _clear_rate_limiter() -> None:
    await get_limiter().clear_all()
    yield
    await get_limiter().clear_all()


@pytest.fixture
def rate_limited_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT_PER_MINUTE", "2")
    get_settings.cache_clear()

    async def _noop_warmup(_self: object) -> None:
        return None

    async def _noop_db() -> None:
        return None

    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.main.check_db_ready", _noop_db)
    monkeypatch.setattr("app.main.warm_all_runtime_caches", _noop_db)
    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_parse_agent_rate_limit_defaults() -> None:
    policy = parse_agent_rate_limit({})
    assert policy.max_messages == 20
    assert policy.window_seconds == 60
    assert "try again" in policy.limit_message.lower()


@pytest.mark.asyncio
async def test_parse_agent_rate_limit_custom() -> None:
    policy = parse_agent_rate_limit(
        {
            "rate_limit": {
                "max_messages": 3,
                "window_seconds": 10,
                "limit_message": "Slow down please.",
            }
        }
    )
    assert policy.max_messages == 3
    assert policy.window_seconds == 10
    assert policy.limit_message == "Slow down please."


@pytest.mark.asyncio
async def test_visitor_message_rate_limit_enforced() -> None:
    from uuid import uuid4

    agent_id = uuid4()
    behavior = {
        "rate_limit": {
            "max_messages": 2,
            "window_seconds": 60,
            "limit_message": "Custom limit hit.",
        }
    }
    await enforce_visitor_message_rate_limit(
        behavior_settings=behavior,
        visitor_id="visitor-a",
        agent_id=agent_id,
    )
    await enforce_visitor_message_rate_limit(
        behavior_settings=behavior,
        visitor_id="visitor-a",
        agent_id=agent_id,
    )
    with pytest.raises(RateLimitError) as exc_info:
        await enforce_visitor_message_rate_limit(
            behavior_settings=behavior,
            visitor_id="visitor-a",
            agent_id=agent_id,
        )
    assert exc_info.value.code == "rate_limit.exceeded"
    assert exc_info.value.message == "Custom limit hit."


def test_http_rate_limit_returns_429(rate_limited_client: TestClient) -> None:
    path = "/api/v1/system/version"
    for _ in range(2):
        res = rate_limited_client.get(path)
        assert res.status_code == 200
    blocked = rate_limited_client.get(path)
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error"]["code"] == "rate_limit.exceeded"
    assert blocked.headers.get("Retry-After")
