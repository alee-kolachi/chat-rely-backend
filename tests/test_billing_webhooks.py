from datetime import UTC

from app.domains.billing.subscription_sync import _extract_subscription_period_bounds


def test_extract_subscription_period_bounds_prefers_subscription_fields() -> None:
    start, end = _extract_subscription_period_bounds(
        {
            "current_period_start": 1_714_000_000,
            "current_period_end": 1_714_086_400,
            "items": {
                "data": [
                    {
                        "current_period_start": 111,
                        "current_period_end": 222,
                    }
                ]
            },
        }
    )

    assert int(start.timestamp()) == 1_714_000_000
    assert int(end.timestamp()) == 1_714_086_400


def test_extract_subscription_period_bounds_falls_back_to_item_fields() -> None:
    start, end = _extract_subscription_period_bounds(
        {
            "items": {
                "data": [
                    {
                        "current_period_start": "1_714_000_000".replace("_", ""),
                        "current_period_end": "1_714_086_400".replace("_", ""),
                    }
                ]
            }
        }
    )

    assert int(start.timestamp()) == 1_714_000_000
    assert int(end.timestamp()) == 1_714_086_400
    assert start.tzinfo == UTC
    assert end.tzinfo == UTC
