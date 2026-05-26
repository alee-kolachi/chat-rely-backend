from app.core.settings import Settings
from app.domains.billing.return_url import resolve_billing_app_base_url


def test_resolve_prefers_matching_browser_origin() -> None:
    settings = Settings(billing_app_base_url="http://localhost:3000")
    assert (
        resolve_billing_app_base_url(
            settings,
            return_origin="https://app.example.com",
            request_origin="https://app.example.com",
        )
        == "https://app.example.com"
    )


def test_resolve_loopback_port_mismatch() -> None:
    settings = Settings(billing_app_base_url="http://localhost:3000")
    assert (
        resolve_billing_app_base_url(
            settings,
            return_origin="http://127.0.0.1:3000",
            request_origin="http://127.0.0.1:3000",
        )
        == "http://127.0.0.1:3000"
    )


def test_resolve_falls_back_to_default() -> None:
    settings = Settings(billing_app_base_url="http://localhost:3000")
    assert (
        resolve_billing_app_base_url(
            settings,
            return_origin="https://evil.example.com",
            request_origin="https://app.example.com",
        )
        == "http://localhost:3000"
    )
