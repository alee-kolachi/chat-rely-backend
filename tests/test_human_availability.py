"""Tests for human escalation availability (live vs email path)."""

from datetime import UTC, datetime

from app.domains.actions.human_availability import (
    SCHEDULE_AND_MANUAL,
    SCHEDULE_ONLY,
    MANUAL_ONLY,
    seller_is_available_for_live_chat,
)


def _cfg(**kwargs: object) -> dict:
    base: dict = {
        "availability_mode": MANUAL_ONLY,
        "manual_online": False,
        "timezone": "UTC",
        "business_day_start": "09:00",
        "business_day_end": "17:00",
        "business_days": [0, 1, 2, 3, 4],
    }
    base.update(kwargs)
    return base


def test_manual_only_respects_toggle() -> None:
    assert seller_is_available_for_live_chat(_cfg(manual_online=False)) is False
    assert seller_is_available_for_live_chat(_cfg(manual_online=True)) is True


def test_schedule_only_weekday_inside_hours() -> None:
    # Monday 2026-05-04 10:00 UTC
    now = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    cfg = _cfg(
        availability_mode=SCHEDULE_ONLY,
        timezone="UTC",
        business_days=[0],  # Monday only for this test — actually May 4 2026 is Monday
    )
    assert now.weekday() == 0
    assert seller_is_available_for_live_chat(cfg, now=now) is True


def test_schedule_only_weekend_outside() -> None:
    # Saturday 2026-05-02
    now = datetime(2026, 5, 2, 10, 0, tzinfo=UTC)
    cfg = _cfg(availability_mode=SCHEDULE_ONLY, timezone="UTC", business_days=[0, 1, 2, 3, 4])
    assert now.weekday() == 5
    assert seller_is_available_for_live_chat(cfg, now=now) is False


def test_schedule_only_outside_hours() -> None:
    now = datetime(2026, 5, 4, 18, 0, tzinfo=UTC)  # Monday 18:00
    cfg = _cfg(availability_mode=SCHEDULE_ONLY, timezone="UTC")
    assert seller_is_available_for_live_chat(cfg, now=now) is False


def test_schedule_and_manual_off_hours_manual_true() -> None:
    now = datetime(2026, 5, 4, 18, 0, tzinfo=UTC)
    cfg = _cfg(
        availability_mode=SCHEDULE_AND_MANUAL,
        manual_online=True,
        timezone="UTC",
    )
    assert seller_is_available_for_live_chat(cfg, now=now) is True


def test_schedule_and_manual_off_hours_manual_false() -> None:
    now = datetime(2026, 5, 4, 18, 0, tzinfo=UTC)
    cfg = _cfg(
        availability_mode=SCHEDULE_AND_MANUAL,
        manual_online=False,
        timezone="UTC",
    )
    assert seller_is_available_for_live_chat(cfg, now=now) is False


def test_unknown_mode_falls_back_to_manual_only_semantics() -> None:
    cfg = _cfg(availability_mode="bogus", manual_online=True)
    assert seller_is_available_for_live_chat(cfg) is True
