import pytest

from app.core.settings import Settings
from app.domains.billing.price_map import (
    canonical_plan_slug,
    monthly_price_id_for_slug,
    paid_checkout_slugs,
    slug_for_price_id,
)


def test_canonical_plan_slug_legacy() -> None:
    assert canonical_plan_slug("starter") == "hobby"
    assert canonical_plan_slug("growth") == "standard"
    assert canonical_plan_slug("hobby") == "hobby"


def test_monthly_price_id_for_slug() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        stripe_price_hobby_monthly="price_h",
        stripe_price_standard_monthly="price_s",
        stripe_price_pro_monthly="price_p",
    )
    assert monthly_price_id_for_slug(s, "hobby") == "price_h"
    assert monthly_price_id_for_slug(s, "standard") == "price_s"
    assert monthly_price_id_for_slug(s, "pro") == "price_p"
    assert monthly_price_id_for_slug(s, "starter") == "price_h"
    assert monthly_price_id_for_slug(s, "growth") == "price_s"
    assert monthly_price_id_for_slug(s, "free") is None


def test_monthly_price_id_legacy_env_fallback() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        stripe_price_starter_monthly="price_legacy_h",
        stripe_price_growth_monthly="price_legacy_s",
    )
    assert monthly_price_id_for_slug(s, "hobby") == "price_legacy_h"
    assert monthly_price_id_for_slug(s, "standard") == "price_legacy_s"


def test_slug_for_price_id_roundtrip() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        stripe_price_hobby_monthly="price_h",
        stripe_price_standard_monthly="price_s",
    )
    assert slug_for_price_id(s, "price_h") == "hobby"
    assert slug_for_price_id(s, "price_s") == "standard"
    assert slug_for_price_id(s, "price_unknown") is None


def test_slug_for_price_id_legacy_starter_growth_ids() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        stripe_price_starter_monthly="price_a",
        stripe_price_growth_monthly="price_b",
    )
    assert slug_for_price_id(s, "price_a") == "hobby"
    assert slug_for_price_id(s, "price_b") == "standard"


def test_paid_checkout_slugs_omits_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "STRIPE_PRICE_HOBBY_MONTHLY",
        "STRIPE_PRICE_STANDARD_MONTHLY",
        "STRIPE_PRICE_PRO_MONTHLY",
        "STRIPE_PRICE_STARTER_MONTHLY",
        "STRIPE_PRICE_GROWTH_MONTHLY",
        "STRIPE_PRICE_SCALE_MONTHLY",
    ):
        monkeypatch.delenv(name, raising=False)
    s = Settings(
        database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
        supabase_jwks_url="https://example.com/.well-known/jwks.json",
        supabase_issuer="https://example.com/auth/v1",
        stripe_price_hobby_monthly="price_a",
        stripe_price_standard_monthly="",
        stripe_price_pro_monthly=None,
        stripe_price_starter_monthly=None,
        stripe_price_growth_monthly=None,
        stripe_price_scale_monthly=None,
    )
    assert paid_checkout_slugs(s) == ("hobby",)
