"""Execute Shopify-backed tool logic (called from LangChain tools)."""

from __future__ import annotations

import re

import structlog

from app.domains.integrations.shopify.admin_client import compact_json, shopify_graphql

log = structlog.get_logger("runtime.shopify.tool_runners")


def _product_edges(data: dict[str, object]) -> list[object]:
    products = data.get("products")
    if not isinstance(products, dict):
        return []
    edges = products.get("edges")
    return list(edges) if isinstance(edges, list) else []


def _needs_broad_catalog_retry(query: str) -> bool:
    """Conversational questions rarely match Shopify Admin `products(query:)` keyword search."""
    s = (query or "").strip().lower()
    if len(s) <= 3:
        return True
    if len(s) > 120:
        return True
    return bool(
        re.search(
            r"(?i)(what\s+(kind|type|sort)s?\s+of|do\s+you\s+(have|sell|carry|offer)|"
            r"products?\s+(do\s+you|can\s+i|are\s+)|your\s+(website|store)|"
            r"\bcatalog\b|\bcollection\b|website|tell\s+me\s+about|"
            r"(check|try|look|search)\s+again|whole\s+range)",
            s,
        )
    )


async def run_product_search(
    *, shop_domain: str, access_token: str, query: str, max_results: int = 5
) -> str:
    q = (query or "").strip()
    if not q:
        return compact_json({"error": "empty_query"})
    n = max(1, min(int(max_results), 20))
    gql = """
    query ProductSearch($q: String!, $n: Int!) {
      products(first: $n, query: $q) {
        edges {
          node {
            title
            handle
            status
            variants(first: 20) {
              edges {
                node {
                  sku
                  title
                  price
                  inventoryQuantity
                }
              }
            }
          }
        }
      }
    }
    """
    body = await shopify_graphql(
        shop_domain=shop_domain,
        access_token=access_token,
        query=gql,
        variables={"q": q, "n": n},
    )
    data = dict(body.get("data") or {})
    initial_count = len(_product_edges(data))
    retried_broad = False

    if not _product_edges(data) and _needs_broad_catalog_retry(q):
        # Admin search treats long natural-language strings poorly; list active published products.
        retried_broad = True
        n2 = max(n, 10)
        body2 = await shopify_graphql(
            shop_domain=shop_domain,
            access_token=access_token,
            query=gql,
            variables={"q": "published_status:published", "n": n2},
        )
        data = dict(body2.get("data") or {})
    final_count = len(_product_edges(data))
    log.info(
        "runtime.shopify_product_search_result",
        query_preview=q[:120],
        initial_count=initial_count,
        retried_broad=retried_broad,
        final_count=final_count,
    )

    return compact_json(data)


async def run_order_lookup(
    *,
    shop_domain: str,
    access_token: str,
    order_name_or_number: str,
    customer_email: str | None = None,
) -> str:
    """Lookup by order name (#1001) or numeric id string; optional email filter."""
    raw = (order_name_or_number or "").strip()
    email = (customer_email or "").strip()
    if not raw and not email:
        return compact_json({"error": "provide_order_or_email"})
    gql = """
    query OrdersLookup($q: String!, $n: Int!) {
      orders(first: $n, query: $q, sortKey: CREATED_AT, reverse: true) {
        edges {
          node {
            id
            name
            email
            displayFulfillmentStatus
            displayFinancialStatus
            createdAt
            fulfillments(first: 5) {
              trackingInfo {
                number
                url
              }
              status
            }
            shippingAddress {
              city
              country
              zip
            }
          }
        }
      }
    }
    """
    q_parts: list[str] = []
    if raw:
        if raw.startswith("#"):
            q_parts.append(f"name:{raw}")
        elif raw.isdigit():
            q_parts.append(f"name:#{raw}")
        else:
            q_parts.append(raw)
    order_part = " ".join(q_parts).strip()
    email_part = f"email:{email}" if email and "@" in email else ""
    candidates = [
        c
        for c in [
            " ".join(part for part in [order_part, email_part] if part).strip(),
            order_part,
            email_part,
        ]
        if c
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    queries = [q for q in candidates if not (q in seen or seen.add(q))]

    attempts: list[dict[str, object]] = []
    best_data: dict[str, object] = {}
    best_count = -1
    chosen_query = ""
    for q in queries:
        body = await shopify_graphql(
            shop_domain=shop_domain,
            access_token=access_token,
            query=gql,
            variables={"q": q, "n": 10},
        )
        data = body.get("data") or {}
        orders = ((data.get("orders") or {}).get("edges") or []) if isinstance(data, dict) else []
        count = len(orders) if isinstance(orders, list) else 0
        attempts.append({"query": q, "order_count": count})
        if count > best_count:
            best_count = count
            best_data = data if isinstance(data, dict) else {}
            chosen_query = q
        if count > 0:
            break

    return compact_json(
        {
            "data": best_data,
            "lookup_meta": {
                "queries_tried": attempts,
                "selected_query": chosen_query,
                "result_count": max(best_count, 0),
                "not_found": best_count <= 0,
            },
        }
    )


async def run_inventory_check(
    *,
    shop_domain: str,
    access_token: str,
    sku: str | None = None,
    product_query: str | None = None,
) -> str:
    sku_q = (sku or "").strip()
    pq = (product_query or "").strip()
    if not sku_q and not pq:
        return compact_json({"error": "provide_sku_or_product_query"})
    gql = """
    query InvProducts($q: String!, $n: Int!) {
      products(first: $n, query: $q) {
        edges {
          node {
            title
            handle
            variants(first: 50) {
              edges {
                node {
                  sku
                  title
                  inventoryQuantity
                  inventoryItem {
                    id
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    q_var = f"sku:{sku_q}" if sku_q else pq
    body = await shopify_graphql(
        shop_domain=shop_domain,
        access_token=access_token,
        query=gql,
        variables={"q": q_var, "n": 5},
    )
    return compact_json(body.get("data") or {})


async def run_customer_context(
    *,
    shop_domain: str,
    access_token: str,
    email: str,
    recent_orders: int = 5,
) -> str:
    em = (email or "").strip()
    if not em or "@" not in em:
        return compact_json({"error": "valid_email_required"})
    ro = max(1, min(int(recent_orders), 25))
    gql = """
    query CustomerCtx($q: String!, $orderCount: Int!) {
      customers(first: 5, query: $q) {
        edges {
          node {
            id
            displayName
            email
            numberOfOrders
            amountSpent {
              amount
              currencyCode
            }
            orders(first: $orderCount, sortKey: CREATED_AT, reverse: true) {
              edges {
                node {
                  name
                  createdAt
                  displayFinancialStatus
                  totalPriceSet {
                    shopMoney {
                      amount
                      currencyCode
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    body = await shopify_graphql(
        shop_domain=shop_domain,
        access_token=access_token,
        query=gql,
        variables={"q": f"email:{em}", "orderCount": ro},
    )
    return compact_json(body.get("data") or {})
