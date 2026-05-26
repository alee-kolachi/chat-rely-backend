"""Expiring offline token exchange (Shopify Dec 2025+)."""

import pytest

from app.domains.integrations.shopify import service as shopify_service


@pytest.mark.asyncio
async def test_exchange_code_requests_expiring_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str] | None] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {
                "access_token": "shpat_test",
                "expires_in": 3600,
                "refresh_token": "shprt_test",
                "refresh_token_expires_in": 7776000,
                "scope": "read_orders",
            }

    class _FakeClient:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, url: str, data: dict[str, str] | None = None, **_kw: object) -> _Resp:
            calls.append(data)
            return _Resp()

    monkeypatch.setattr(shopify_service.httpx, "AsyncClient", _FakeClient)
    out = await shopify_service.exchange_code_for_token(shop_domain="test.myshopify.com", code="abc")
    assert calls and calls[0] is not None
    assert calls[0].get("expiring") == "1"
    assert out["access_token"] == "shpat_test"
    assert out.get("refresh_token")
