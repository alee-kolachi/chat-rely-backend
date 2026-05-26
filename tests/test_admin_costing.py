"""Tests for Phase 3 admin costing.

Covers three layers:
  1. Settings: env JSON parsing, key validation, empty defaults.
  2. Pure helpers in app.domains.admin.costing.
  3. Service-layer routes (with monkeypatched DB / cost service).

The DB-touching paths in costing_service.py are exercised against the live
schema in the rest of the suite; here we focus on logic + wiring."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import AuthError
from app.core.settings import Settings, get_settings
from app.domains.admin.costing import (
    build_embedding_cost_usd_expr,
    build_llm_cost_usd_expr,
    compute_embedding_cost_usd,
    compute_message_cost_usd,
    embedding_pricing_status,
    unknown_models_warning,
)
from app.domains.admin import costing_service
from app.domains.admin.schemas import (
    AdminConversationCost,
    AdminCostByAgentRow,
    AdminCostByModelRow,
    AdminCostingLeaderboard,
    AdminCostingPeriod,
    AdminMessageCostRow,
    AdminPlatformCosting,
    AdminUserCosting,
    AdminUserCostingRow,
)
from app.main import create_app


ADMIN_EMAIL = "alice@chatrely.com"
ADMIN_USER_ID = "00000000-0000-0000-0000-000000000aaa"
NON_ADMIN_EMAIL = "bob@example.com"
NON_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000bbb"


class _DummyVerifier:
    def verify_token(self, token: str) -> dict[str, str]:
        if token == "admin-token":
            return {"sub": ADMIN_USER_ID, "email": ADMIN_EMAIL}
        if token == "non-admin-token":
            return {"sub": NON_ADMIN_USER_ID, "email": NON_ADMIN_EMAIL}
        raise AuthError("bad token")


async def _noop_warmup(_self: object) -> None:
    return None


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setenv(
        "LLM_INPUT_PRICE_PER_MILLION_USD",
        '{"gpt-4o-mini":0.15,"gpt-4o":2.50}',
    )
    monkeypatch.setenv(
        "LLM_OUTPUT_PRICE_PER_MILLION_USD",
        '{"gpt-4o-mini":0.60,"gpt-4o":10.0}',
    )
    monkeypatch.setenv(
        "EMBEDDING_PRICE_PER_MILLION_USD",
        '{"text-embedding-3-small":0.02}',
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.security.TokenVerifier.warmup", _noop_warmup)
    monkeypatch.setattr("app.api.deps.get_token_verifier", lambda: _DummyVerifier())
    costing_service._clear_cache()
    app = create_app()
    with TestClient(app) as client:
        yield client
    costing_service._clear_cache()
    get_settings.cache_clear()


def _admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


def _non_admin_auth() -> dict[str, str]:
    return {"Authorization": "Bearer non-admin-token"}


# --- 1. Settings parsing ---------------------------------------------------------


def test_settings_parses_valid_price_map_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_EMAILS", "alice@x.com")
    monkeypatch.setenv("DEV_AUTH_BYPASS_ENABLED", "false")
    monkeypatch.setenv(
        "LLM_INPUT_PRICE_PER_MILLION_USD",
        '{"gpt-4o-mini":0.15,"gpt-4o":2.5}',
    )
    monkeypatch.setenv(
        "LLM_OUTPUT_PRICE_PER_MILLION_USD",
        '{"gpt-4o-mini":0.6}',
    )
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_input_price_per_million_usd == {"gpt-4o-mini": 0.15, "gpt-4o": 2.5}
    assert s.llm_output_price_per_million_usd == {"gpt-4o-mini": 0.6}
    get_settings.cache_clear()


def test_settings_empty_env_yields_empty_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deleting only os.environ keys does not remove values loaded from ``.env``; force empty maps.
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", "{}")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_USD", "{}")
    monkeypatch.setenv("EMBEDDING_PRICE_PER_MILLION_USD", "{}")
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_input_price_per_million_usd == {}
    assert s.llm_output_price_per_million_usd == {}
    assert s.embedding_price_per_million_usd == {}
    get_settings.cache_clear()


def test_compute_embedding_cost_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv(
        "EMBEDDING_PRICE_PER_MILLION_USD",
        '{"text-embedding-3-small":0.02}',
    )
    get_settings.cache_clear()
    s = get_settings()
    assert compute_embedding_cost_usd(s, 0) == 0.0
    assert abs(compute_embedding_cost_usd(s, 500_000) - 0.01) < 1e-9
    assert compute_embedding_cost_usd(s, 100) is not None
    get_settings.cache_clear()


def test_settings_rejects_injection_in_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Single-quote + semicolon would break out of a SQL string literal.
    monkeypatch.setenv(
        "LLM_INPUT_PRICE_PER_MILLION_USD",
        '{"gpt-4o\'; drop table messages":0.1}',
    )
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "invalid model key" in str(exc.value)
    get_settings.cache_clear()


def test_admin_conversation_cost_accepts_event_ledger_fields() -> None:
    cid = uuid4()
    mid = uuid4()
    obj = AdminConversationCost(
        conversation_id=cid,
        total_input_tokens=0,
        total_output_tokens=0,
        total_cost_usd=None,
        by_model=[],
        messages=[
            AdminMessageCostRow(
                id=mid,
                role="user",
                model=None,
                input_tokens=0,
                output_tokens=0,
                cost_usd=None,
                created_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
            )
        ],
        has_unknown_models=False,
    )
    assert obj.cost_events == []
    assert obj.events_total_cost_usd is None
    assert obj.by_kind == []


def test_settings_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", "{not-json")
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "valid JSON" in str(exc.value)
    get_settings.cache_clear()


def test_settings_rejects_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", "[1,2,3]")
    get_settings.cache_clear()
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "JSON object" in str(exc.value)
    get_settings.cache_clear()


# --- 2. Pure helpers -------------------------------------------------------------


def test_build_llm_cost_usd_expr_renders_case_for_each_model() -> None:
    sql = build_llm_cost_usd_expr(
        in_prices={"gpt-4o-mini": 0.15, "gpt-4o": 2.5},
        out_prices={"gpt-4o-mini": 0.6, "gpt-4o": 10.0},
    )
    # Both models are present, prices are inlined as plain decimal floats.
    assert "WHEN m.model = 'gpt-4o-mini'" in sql
    assert "WHEN m.model = 'gpt-4o'" in sql
    assert "0.15" in sql and "0.6" in sql
    assert "2.5" in sql and "10.0" in sql
    assert sql.endswith("/ 1000000.0")
    # NULL fallback for unknown models.
    assert "ELSE NULL" in sql


def test_build_llm_cost_usd_expr_with_empty_maps_returns_null_literal() -> None:
    sql = build_llm_cost_usd_expr(in_prices={}, out_prices={})
    assert "NULL" in sql
    assert "CASE" not in sql


def test_build_llm_cost_usd_expr_validates_keys() -> None:
    with pytest.raises(ValueError):
        build_llm_cost_usd_expr(in_prices={"bad'; drop": 0.1}, out_prices={})


def test_build_embedding_cost_usd_expr_uses_global_model_price() -> None:
    sql = build_embedding_cost_usd_expr(
        embedding_prices={"text-embedding-3-small": 0.02},
        embedding_model="text-embedding-3-small",
    )
    assert "kc.token_count" in sql
    assert "0.02" in sql
    assert sql.endswith("/ 1000000.0")


def test_build_embedding_cost_usd_expr_returns_null_when_model_unpriced() -> None:
    sql = build_embedding_cost_usd_expr(
        embedding_prices={"other-model": 0.05},
        embedding_model="text-embedding-3-small",
    )
    assert "NULL" in sql


def _make_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    in_map: str = '{"gpt-4o-mini":0.15}',
    out_map: str = '{"gpt-4o-mini":0.6}',
    embed_map: str = '{"text-embedding-3-small":0.02}',
) -> Settings:
    monkeypatch.setenv("LLM_INPUT_PRICE_PER_MILLION_USD", in_map)
    monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_MILLION_USD", out_map)
    monkeypatch.setenv("EMBEDDING_PRICE_PER_MILLION_USD", embed_map)
    get_settings.cache_clear()
    s = get_settings()
    return s


def test_compute_message_cost_known_model(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _make_settings(monkeypatch)
    # 1000 in @ 0.15/M + 500 out @ 0.6/M = 0.00015 + 0.0003 = 0.00045
    cost = compute_message_cost_usd(s, "gpt-4o-mini", 1000, 500)
    assert cost is not None
    assert abs(cost - 0.00045) < 1e-9
    get_settings.cache_clear()


def test_compute_message_cost_unknown_model_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _make_settings(monkeypatch)
    assert compute_message_cost_usd(s, "claude-opus", 1000, 1000) is None
    get_settings.cache_clear()


def test_compute_message_cost_no_model_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _make_settings(monkeypatch)
    assert compute_message_cost_usd(s, None, 100, 100) is None
    assert compute_message_cost_usd(s, "", 100, 100) is None
    get_settings.cache_clear()


def test_unknown_models_warning_collects_only_unpriced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = _make_settings(monkeypatch)
    observed = ["gpt-4o-mini", "claude-opus", None, "gpt-4o-mini", "mystery-1"]
    warning = unknown_models_warning(observed, s)
    assert warning == ["claude-opus", "mystery-1"]
    get_settings.cache_clear()


def test_embedding_pricing_status(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _make_settings(monkeypatch)
    priced, model = embedding_pricing_status(s)
    assert priced is True
    assert model == s.openai_embedding_model
    get_settings.cache_clear()


def test_embedding_pricing_status_unpriced(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _make_settings(monkeypatch, embed_map='{"unrelated-model":0.5}')
    priced, model = embedding_pricing_status(s)
    assert priced is False
    assert model == s.openai_embedding_model
    get_settings.cache_clear()


# --- 3. Routes -------------------------------------------------------------------


def _platform_overview() -> AdminPlatformCosting:
    cur_start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    cur_end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    prior_start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prior_end = cur_start
    return AdminPlatformCosting(
        period_label="MTD vs prior month",
        current=AdminCostingPeriod(
            label="MTD",
            period_start=cur_start,
            period_end=cur_end,
            revenue_usd=1000.0,
            llm_cost_usd=120.0,
            embedding_cost_usd=5.0,
            total_cost_usd=125.0,
            gross_margin_usd=875.0,
            gross_margin_pct=87.5,
        ),
        prior=AdminCostingPeriod(
            label="Prior month",
            period_start=prior_start,
            period_end=prior_end,
            revenue_usd=900.0,
            llm_cost_usd=100.0,
            embedding_cost_usd=4.0,
            total_cost_usd=104.0,
            gross_margin_usd=796.0,
            gross_margin_pct=88.4,
        ),
        by_model=[
            AdminCostByModelRow(
                model="gpt-4o-mini",
                input_tokens=10_000_000,
                output_tokens=5_000_000,
                cost_usd=4.5,
                pct_of_total=3.75,
            )
        ],
        unknown_models=["claude-opus"],
        embedding_model="text-embedding-3-small",
        embedding_model_priced=True,
        cached_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        cache_ttl_seconds=60,
    )


def test_overview_returns_full_shape(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(_db: Any) -> AdminPlatformCosting:
        return _platform_overview()

    monkeypatch.setattr(
        "app.api.routes.admin.costing.get_platform_costing_overview", fake
    )
    response = admin_client.get("/api/v1/admin/costing/overview", headers=_admin_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["current"]["llm_cost_usd"] == 120.0
    assert body["prior"]["revenue_usd"] == 900.0
    assert body["unknown_models"] == ["claude-opus"]
    assert body["by_model"][0]["model"] == "gpt-4o-mini"
    assert body["embedding_model"] == "text-embedding-3-small"


def test_overview_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/costing/overview", headers=_non_admin_auth()
    )
    assert response.status_code == 404


def _leaderboard_row(email: str, margin: float, cost: float) -> AdminUserCostingRow:
    return AdminUserCostingRow(
        user_id=uuid4(),
        email=email,
        plan_slug="standard",
        plan_name="Growth",
        revenue_usd=29.0,
        llm_cost_usd=cost,
        embedding_cost_usd=0.0,
        total_cost_usd=cost,
        margin_usd=margin,
        margin_pct=(margin / 29.0 * 100.0) if 29.0 > 0 else None,
    )


def test_leaderboard_worst_margin_routes_to_worst(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_worst(_db: Any, *, limit: int) -> AdminCostingLeaderboard:
        captured["metric"] = "worst_margin"
        captured["limit"] = limit
        return AdminCostingLeaderboard(
            metric="worst_margin",
            items=[
                _leaderboard_row("burner@x.com", -50.0, 79.0),
                _leaderboard_row("steady@x.com", 10.0, 19.0),
            ],
            cached_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
            cache_ttl_seconds=60,
        )

    async def fake_top(_db: Any, *, limit: int) -> AdminCostingLeaderboard:
        captured["metric"] = "top_spend"
        return AdminCostingLeaderboard(
            metric="top_spend",
            items=[],
            cached_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
            cache_ttl_seconds=60,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.costing.get_worst_margin_leaderboard", fake_worst
    )
    monkeypatch.setattr(
        "app.api.routes.admin.costing.get_top_spenders_leaderboard", fake_top
    )

    response = admin_client.get(
        "/api/v1/admin/costing/leaderboard",
        params={"metric": "worst_margin", "limit": 5},
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "worst_margin"
    assert body["items"][0]["email"] == "burner@x.com"
    assert body["items"][0]["margin_usd"] == -50.0
    assert captured == {"metric": "worst_margin", "limit": 5}


def test_leaderboard_top_spend_routes_to_top(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_worst(_db: Any, *, limit: int) -> AdminCostingLeaderboard:
        raise AssertionError("worst_margin endpoint should not be invoked")

    async def fake_top(_db: Any, *, limit: int) -> AdminCostingLeaderboard:
        return AdminCostingLeaderboard(
            metric="top_spend",
            items=[_leaderboard_row("whale@x.com", -200.0, 229.0)],
            cached_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
            cache_ttl_seconds=60,
        )

    monkeypatch.setattr(
        "app.api.routes.admin.costing.get_worst_margin_leaderboard", fake_worst
    )
    monkeypatch.setattr(
        "app.api.routes.admin.costing.get_top_spenders_leaderboard", fake_top
    )

    response = admin_client.get(
        "/api/v1/admin/costing/leaderboard",
        params={"metric": "top_spend"},
        headers=_admin_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "top_spend"
    assert body["items"][0]["email"] == "whale@x.com"


def test_leaderboard_invalid_metric_returns_422(admin_client: TestClient) -> None:
    response = admin_client.get(
        "/api/v1/admin/costing/leaderboard",
        params={"metric": "made_up"},
        headers=_admin_auth(),
    )
    assert response.status_code == 422


def test_user_costing_returns_per_agent_breakdown(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_id = uuid4()
    agent_a = uuid4()
    agent_b = uuid4()

    async def fake(_db: Any, user_id: UUID) -> AdminUserCosting:
        assert user_id == target_id
        return AdminUserCosting(
            user_id=target_id,
            email="alice@x.com",
            period_label="MTD",
            revenue_usd=29.0,
            llm_cost_usd=12.5,
            embedding_cost_usd=1.5,
            total_cost_usd=14.0,
            margin_usd=15.0,
            margin_pct=(15.0 / 29.0) * 100.0,
            by_agent=[
                AdminCostByAgentRow(
                    agent_id=agent_a,
                    agent_name="Support",
                    llm_cost_usd=10.0,
                    embedding_cost_usd=1.0,
                    total_cost_usd=11.0,
                    conversations=5,
                    messages=120,
                ),
                AdminCostByAgentRow(
                    agent_id=agent_b,
                    agent_name="Sales",
                    llm_cost_usd=2.5,
                    embedding_cost_usd=0.5,
                    total_cost_usd=3.0,
                    conversations=2,
                    messages=30,
                ),
            ],
        )

    monkeypatch.setattr("app.api.routes.admin.costing.get_user_costing", fake)
    response = admin_client.get(
        f"/api/v1/admin/users/{target_id}/costing", headers=_admin_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["margin_usd"] == 15.0
    by_agent = {r["agent_name"]: r["total_cost_usd"] for r in body["by_agent"]}
    # Per-agent must sum back to the user total cost.
    assert sum(by_agent.values()) == 14.0


def test_user_costing_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        f"/api/v1/admin/users/{uuid4()}/costing", headers=_non_admin_auth()
    )
    assert response.status_code == 404


def test_conversation_cost_returns_messages(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conv_id = uuid4()
    msg_id = uuid4()

    async def fake(_db: Any, conversation_id: UUID) -> AdminConversationCost:
        assert conversation_id == conv_id
        return AdminConversationCost(
            conversation_id=conv_id,
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cost_usd=0.00045,
            by_model=[
                AdminCostByModelRow(
                    model="gpt-4o-mini",
                    input_tokens=1000,
                    output_tokens=500,
                    cost_usd=0.00045,
                    pct_of_total=100.0,
                )
            ],
            messages=[
                AdminMessageCostRow(
                    id=msg_id,
                    role="assistant",
                    model="gpt-4o-mini",
                    input_tokens=1000,
                    output_tokens=500,
                    cost_usd=0.00045,
                    created_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
                )
            ],
            has_unknown_models=False,
        )

    monkeypatch.setattr("app.api.routes.admin.costing.get_conversation_cost", fake)
    response = admin_client.get(
        f"/api/v1/admin/conversations/{conv_id}/cost", headers=_admin_auth()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_input_tokens"] == 1000
    assert abs(body["total_cost_usd"] - 0.00045) < 1e-9
    assert body["by_model"][0]["pct_of_total"] == 100.0
    assert body["messages"][0]["cost_usd"] == 0.00045


def test_conversation_cost_non_admin_returns_404(admin_client: TestClient) -> None:
    response = admin_client.get(
        f"/api/v1/admin/conversations/{uuid4()}/cost",
        headers=_non_admin_auth(),
    )
    assert response.status_code == 404


# --- TTL cache -------------------------------------------------------------------


def test_cache_serves_stale_within_ttl() -> None:
    """Calling get_platform_costing_overview twice should hit the in-process cache."""
    costing_service._clear_cache()
    overview = _platform_overview()
    costing_service._cache_put("platform_overview", overview)
    cached = costing_service._cache_get("platform_overview")
    assert cached is overview


def test_cache_expires_when_ttl_negative() -> None:
    costing_service._clear_cache()
    overview = _platform_overview()
    costing_service._cache_put("platform_overview", overview, ttl=-1)
    assert costing_service._cache_get("platform_overview") is None
