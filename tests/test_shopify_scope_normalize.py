from app.domains.integrations.shopify.service import _normalize_scope_from_oauth_response


def test_normalize_scope_string() -> None:
    assert _normalize_scope_from_oauth_response(
        {"access_token": "x", "scope": "read_products, read_orders"}
    ) == "read_products, read_orders"


def test_normalize_scope_list() -> None:
    assert _normalize_scope_from_oauth_response(
        {"access_token": "x", "scope": ["read_products", "read_orders"]}
    ) == "read_products,read_orders"


def test_normalize_scopes_plural_key() -> None:
    assert _normalize_scope_from_oauth_response(
        {"access_token": "x", "scopes": "read_products,read_customers"}
    ) == "read_products,read_customers"


def test_normalize_missing_returns_none() -> None:
    assert _normalize_scope_from_oauth_response({"access_token": "x"}) is None
