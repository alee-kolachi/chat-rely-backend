"""Configure the global Stripe API key from settings."""

from __future__ import annotations

import stripe

from app.core.settings import get_settings


def configure_stripe() -> None:
    key = (get_settings().stripe_secret_key or "").strip()
    stripe.api_key = key or None
