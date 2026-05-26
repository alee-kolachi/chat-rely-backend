import json

import pytest

from app.domains.integrations.shopify import tool_runners


@pytest.mark.asyncio
async def test_order_lookup_falls_back_to_email_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _fake_shopify_graphql(**kwargs):
        q = str((kwargs.get("variables") or {}).get("q") or "")
        calls.append(q)
        if q == "name:#1041 email:test@example.com":
            return {"data": {"orders": {"edges": []}}}
        if q == "name:#1041":
            return {"data": {"orders": {"edges": []}}}
        if q == "email:test@example.com":
            return {"data": {"orders": {"edges": [{"node": {"name": "#1041"}}]}}}
        return {"data": {"orders": {"edges": []}}}

    monkeypatch.setattr(tool_runners, "shopify_graphql", _fake_shopify_graphql)
    out = await tool_runners.run_order_lookup(
        shop_domain="example.myshopify.com",
        access_token="tok",
        order_name_or_number="1041",
        customer_email="test@example.com",
    )
    payload = json.loads(out)
    assert calls == ["name:#1041 email:test@example.com", "name:#1041", "email:test@example.com"]
    assert payload["lookup_meta"]["result_count"] == 1
    assert payload["lookup_meta"]["selected_query"] == "email:test@example.com"
    assert payload["lookup_meta"]["not_found"] is False


@pytest.mark.asyncio
async def test_order_lookup_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_shopify_graphql(**kwargs):
        return {"data": {"orders": {"edges": []}}}

    monkeypatch.setattr(tool_runners, "shopify_graphql", _fake_shopify_graphql)
    out = await tool_runners.run_order_lookup(
        shop_domain="example.myshopify.com",
        access_token="tok",
        order_name_or_number="1041",
        customer_email="none@example.com",
    )
    payload = json.loads(out)
    assert payload["lookup_meta"]["result_count"] == 0
    assert payload["lookup_meta"]["not_found"] is True
    assert len(payload["lookup_meta"]["queries_tried"]) >= 1
