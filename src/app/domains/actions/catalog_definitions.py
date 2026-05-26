"""Static catalog of integration actions (merge with DB agent_actions + plan + scopes)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StaticActionDefinition:
    provider: str
    action_key: str
    label: str
    description: str
    """True when backend implements runtime behavior for this action."""
    code_ready: bool
    """OAuth scopes required on the Shopify token when this action is Shopify-backed."""
    required_scopes: frozenset[str]
    """When True, enabling the action requires Shopify connection, plan feature, and scopes."""
    requires_shopify_connection: bool = True


SHOPIFY_ACTIONS: tuple[StaticActionDefinition, ...] = (
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.product_search",
        label="Product Search",
        description="Search the store catalog by name, tag, or SKU.",
        code_ready=True,
        required_scopes=frozenset({"read_products"}),
        requires_shopify_connection=True,
    ),
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.order_lookup",
        label="Order Lookup",
        description="Track orders and fulfillment status.",
        code_ready=True,
        required_scopes=frozenset({"read_orders", "read_fulfillments"}),
        requires_shopify_connection=True,
    ),
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.inventory_check",
        label="Inventory Check",
        description="Stock levels per variant using inventory levels.",
        code_ready=True,
        required_scopes=frozenset({"read_inventory"}),
        requires_shopify_connection=True,
    ),
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.customer_context",
        label="Customer Profile",
        description="Customer details and recent orders.",
        code_ready=True,
        required_scopes=frozenset({"read_customers", "read_orders"}),
        requires_shopify_connection=True,
    ),
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.refund_status",
        label="Refund and Return Status",
        description="Return and refund progress (requires additional Shopify scopes).",
        code_ready=False,
        required_scopes=frozenset({"read_orders", "read_returns"}),
        requires_shopify_connection=True,
    ),
    StaticActionDefinition(
        provider="shopify",
        action_key="shopify.cart_recovery",
        label="Abandoned Cart Recovery",
        description="Resume unfinished checkouts (requires read_checkouts scope).",
        code_ready=False,
        required_scopes=frozenset({"read_checkouts", "read_customers"}),
        requires_shopify_connection=True,
    ),
)

HUMAN_ACTIONS: tuple[StaticActionDefinition, ...] = (
    StaticActionDefinition(
        provider="human",
        action_key="human.escalate",
        label="Escalate to Human",
        description="Allow handoff to your team with live ETA or email follow-up when the AI escalates.",
        code_ready=True,
        required_scopes=frozenset(),
        requires_shopify_connection=False,
    ),
)

# Future integrations — placeholders in catalog until implemented.
STUB_ACTIONS: tuple[StaticActionDefinition, ...] = (
    StaticActionDefinition(
        provider="email",
        action_key="email.bridge",
        label="Email bridge",
        description="Send and receive customer email through your provider (Mailjet).",
        code_ready=False,
        required_scopes=frozenset(),
        requires_shopify_connection=False,
    ),
    StaticActionDefinition(
        provider="zendesk",
        action_key="zendesk.tickets",
        label="Zendesk tickets",
        description="Sync escalations with Zendesk.",
        code_ready=False,
        required_scopes=frozenset(),
        requires_shopify_connection=False,
    ),
    StaticActionDefinition(
        provider="calendly",
        action_key="calendly.booking",
        label="Calendly booking",
        description="Offer scheduling links from the assistant.",
        code_ready=False,
        required_scopes=frozenset(),
        requires_shopify_connection=False,
    ),
)


def all_static_definitions() -> list[StaticActionDefinition]:
    return list(SHOPIFY_ACTIONS + HUMAN_ACTIONS + STUB_ACTIONS)


def get_static_definition(action_key: str) -> StaticActionDefinition | None:
    for d in all_static_definitions():
        if d.action_key == action_key:
            return d
    return None
