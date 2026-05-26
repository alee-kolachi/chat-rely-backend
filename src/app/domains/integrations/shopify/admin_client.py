"""Minimal Shopify Admin GraphQL client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.errors import AppError
from app.core.settings import get_settings


async def shopify_graphql(
    *,
    shop_domain: str,
    access_token: str,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    ver = settings.shopify_api_version
    url = f"https://{shop_domain}/admin/api/{ver}/graphql.json"
    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    payload = {"query": query, "variables": variables or {}}
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise AppError(
            code="shopify.graphql_http",
            message="Shopify GraphQL request failed",
            status_code=502,
            details={"status": response.status_code, "body": response.text[:400]},
        )
    body = response.json()
    if body.get("errors"):
        raise AppError(
            code="shopify.graphql_error",
            message="Shopify GraphQL returned errors",
            status_code=502,
            details={"errors": body["errors"][:5]},
        )
    return body


def compact_json(data: Any, limit: int = 12000) -> str:
    s = json.dumps(data, ensure_ascii=False, default=str)
    if len(s) > limit:
        return s[: limit - 3] + "..."
    return s
