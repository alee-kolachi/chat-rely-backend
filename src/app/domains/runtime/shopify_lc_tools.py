"""LangChain StructuredTools for Shopify Admin actions."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.domains.integrations.shopify.tool_runners import (
    run_customer_context,
    run_inventory_check,
    run_order_lookup,
    run_product_search,
)


class ProductSearchInput(BaseModel):
    query: str = Field(
        description=(
            "Shopify Admin product search query (keywords, SKU, tag). "
            "For broad ‘what do you sell / browse the catalog’ questions, use exactly: published_status:published"
        )
    )
    max_results: int = Field(default=5, ge=1, le=20)


class OrderLookupInput(BaseModel):
    order_name_or_number: str = Field(
        default="",
        description="Order number as shown to the customer (e.g. 1001 or #1001). Leave empty if using email only.",
    )
    customer_email: str | None = Field(default=None, description="Customer email to narrow order search.")


class InventoryInput(BaseModel):
    sku: str | None = Field(default=None, description="Variant SKU if known.")
    product_query: str | None = Field(default=None, description="Product name or keywords if SKU unknown.")


class CustomerContextInput(BaseModel):
    email: str = Field(description="Customer email address.")
    recent_orders: int = Field(default=5, ge=1, le=25, description="How many recent orders to include.")


def build_shopify_langchain_tools(
    shop_domain: str,
    access_token: str,
    enabled_action_keys: set[str],
) -> list[StructuredTool]:
    tools: list[StructuredTool] = []

    if "shopify.product_search" in enabled_action_keys:

        async def _product_search(query: str, max_results: int = 5) -> str:
            return await run_product_search(
                shop_domain=shop_domain,
                access_token=access_token,
                query=query,
                max_results=max_results,
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_product_search,
                name="shopify_product_search",
                description=(
                    "Search the merchant's Shopify catalog for products, variants, SKUs, and prices. "
                    "Use when shoppers ask what you sell, availability, recommendations, or pricing."
                ),
                args_schema=ProductSearchInput,
            )
        )

    if "shopify.order_lookup" in enabled_action_keys:

        async def _order_lookup(order_name_or_number: str = "", customer_email: str | None = None) -> str:
            return await run_order_lookup(
                shop_domain=shop_domain,
                access_token=access_token,
                order_name_or_number=order_name_or_number,
                customer_email=customer_email,
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_order_lookup,
                name="shopify_order_lookup",
                description=(
                    "Look up order status, fulfillment, and tracking using order number and/or customer email."
                ),
                args_schema=OrderLookupInput,
            )
        )

    if "shopify.inventory_check" in enabled_action_keys:

        async def _inventory(sku: str | None = None, product_query: str | None = None) -> str:
            return await run_inventory_check(
                shop_domain=shop_domain,
                access_token=access_token,
                sku=sku,
                product_query=product_query,
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_inventory,
                name="shopify_inventory_check",
                description=(
                    "Check inventory quantities for product variants using SKU or product search terms."
                ),
                args_schema=InventoryInput,
            )
        )

    if "shopify.customer_context" in enabled_action_keys:

        async def _customer(email: str, recent_orders: int = 5) -> str:
            return await run_customer_context(
                shop_domain=shop_domain,
                access_token=access_token,
                email=email,
                recent_orders=recent_orders,
            )

        tools.append(
            StructuredTool.from_function(
                coroutine=_customer,
                name="shopify_customer_context",
                description=(
                    "Load customer profile and recent order history by email for personalization and support."
                ),
                args_schema=CustomerContextInput,
            )
        )

    return tools


def tools_by_name(tools: list[StructuredTool]) -> dict[str, StructuredTool]:
    return {t.name: t for t in tools if getattr(t, "name", None)}
