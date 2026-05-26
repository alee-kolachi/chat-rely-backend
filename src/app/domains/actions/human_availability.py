"""Interpret Escalate to Human action config: when the seller counts as available for live ETA vs email path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Stored in agent_actions.config["availability_mode"]
MANUAL_ONLY = "manual_only"
SCHEDULE_ONLY = "schedule_only"
SCHEDULE_AND_MANUAL = "schedule_and_manual"
DEFAULT_MODE = MANUAL_ONLY


def _parse_hhmm(s: str) -> int:
    """Minutes from midnight (0-1439)."""
    raw = (s or "").strip()
    if not raw:
        return 0
    parts = raw.split(":")
    h = int(parts[0]) if parts[0] else 0
    m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    return max(0, min(1439, h * 60 + m))


def _coerce_business_days(cfg: dict[str, Any]) -> list[int]:
    raw = cfg.get("business_days")
    if isinstance(raw, list) and raw:
        out: list[int] = []
        for x in raw:
            try:
                d = int(x)
                if 0 <= d <= 6:
                    out.append(d)
            except (TypeError, ValueError):
                continue
        return sorted(set(out)) if out else [0, 1, 2, 3, 4]
    return [0, 1, 2, 3, 4]


def _in_business_hours(cfg: dict[str, Any], now: datetime) -> bool:
    tz_name = (cfg.get("timezone") or "UTC").strip()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = UTC

    if now.tzinfo is None:
        local = now.replace(tzinfo=UTC).astimezone(tz)
    else:
        local = now.astimezone(tz)

    weekday = local.weekday()  # Monday = 0 … Sunday = 6
    days = _coerce_business_days(cfg)
    if weekday not in days:
        return False

    start_m = _parse_hhmm(str(cfg.get("business_day_start") or "09:00"))
    end_m = _parse_hhmm(str(cfg.get("business_day_end") or "17:00"))
    cur = local.hour * 60 + local.minute

    if end_m < start_m:
        # Overnight window (e.g. 22:00–06:00)
        return cur >= start_m or cur <= end_m
    # Inclusive both ends: e.g. 09:00–17:00 includes 17:00
    return start_m <= cur <= end_m


def seller_is_available_for_live_chat(
    cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """
    True when we should treat the seller as \"online\" for live ETA / in-app chat expectation.

    Config keys (agent_actions.config):
    - availability_mode: manual_only | schedule_only | schedule_and_manual (default manual_only)
    - manual_online: bool — \"Available now\" toggle
    - timezone: IANA zone for business hours
    - business_day_start / business_day_end: \"HH:MM\" (local)
    - business_days: list[int] weekdays Monday=0 … Sunday=6
    """
    if now is None:
        now = datetime.now(UTC)

    mode = str(cfg.get("availability_mode") or DEFAULT_MODE).strip()
    if mode not in (MANUAL_ONLY, SCHEDULE_ONLY, SCHEDULE_AND_MANUAL):
        mode = DEFAULT_MODE

    manual = bool(cfg.get("manual_online", False))

    if mode == MANUAL_ONLY:
        return manual

    in_hours = _in_business_hours(cfg, now)

    if mode == SCHEDULE_ONLY:
        return in_hours

    return manual or in_hours
