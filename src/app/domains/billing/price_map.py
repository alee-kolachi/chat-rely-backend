"""Map plan slugs to Stripe Price IDs from settings."""

from __future__ import annotations

from app.core.settings import Settings, get_settings

_LEGACY_SLUG_TO_CANONICAL: dict[str, str] = {
    "starter": "hobby",
    "growth": "standard",
}


def canonical_plan_slug(plan_slug: str) -> str:
    """Normalize legacy Stripe/DB slugs to current catalog slugs."""
    s = (plan_slug or "").strip().lower()
    return _LEGACY_SLUG_TO_CANONICAL.get(s, s)


def monthly_price_id_for_slug(settings: Settings, plan_slug: str) -> str | None:
    slug = canonical_plan_slug(plan_slug.strip().lower())
    if slug == "hobby":
        return _nz(settings.stripe_price_hobby_monthly) or _nz(settings.stripe_price_starter_monthly)
    if slug == "standard":
        return _nz(settings.stripe_price_standard_monthly) or _nz(settings.stripe_price_growth_monthly)
    if slug == "pro":
        return _nz(settings.stripe_price_pro_monthly)
    if slug == "scale":
        return _nz(settings.stripe_price_scale_monthly)
    return None


def slug_for_price_id(settings: Settings, price_id: str) -> str | None:
    pid = (price_id or "").strip()
    if not pid:
        return None
    if _nz(settings.stripe_price_hobby_monthly) == pid or _nz(settings.stripe_price_starter_monthly) == pid:
        return "hobby"
    if _nz(settings.stripe_price_standard_monthly) == pid or _nz(settings.stripe_price_growth_monthly) == pid:
        return "standard"
    if _nz(settings.stripe_price_pro_monthly) == pid:
        return "pro"
    if _nz(settings.stripe_price_scale_monthly) == pid:
        return "scale"
    return None


def paid_checkout_slugs(settings: Settings | None = None) -> tuple[str, ...]:
    s = settings or get_settings()
    out: list[str] = []
    for slug in ("hobby", "standard", "pro"):
        if monthly_price_id_for_slug(s, slug):
            out.append(slug)
    return tuple(out)


def _nz(v: str | None) -> str | None:
    x = (v or "").strip()
    return x or None
