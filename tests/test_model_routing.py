"""Unit tests for plan-aware model routing."""

from __future__ import annotations

import pytest

from app.agent.model_routing import (
    ClassifierResult,
    is_likely_greeting_or_small_talk,
    resolve_turn_model_sync,
    should_run_classifier,
)
from app.domains.plans.plan_limits import PlanModelPolicy, advanced_resolution_band
from app.domains.runtime.service import _apply_usage_limit_model_downgrade


def _policy(
    slug: str = "standard",
    *,
    included_premium: int = 250,
) -> PlanModelPolicy:
    return PlanModelPolicy(
        plan_slug=slug,
        default_chat_model="gpt-4o-mini",
        premium_chat_model="gpt-4o",
        included_premium_turns=included_premium,
        throttle_policy={"strong_delay_ms": 5000, "soft_delay_ms": 2000},
    )


def test_greeting_skips_classifier() -> None:
    assert is_likely_greeting_or_small_talk("Hi there")
    assert is_likely_greeting_or_small_talk("thanks!")
    assert not is_likely_greeting_or_small_talk(
        "Where is my order #1234 and why was it marked delivered when I never got it?"
    )


def test_should_run_classifier_hobby_never() -> None:
    policy = _policy("hobby", included_premium=0)
    assert not should_run_classifier(
        policy=policy,
        throttle_tier="normal",
        premium_remaining=0,
        conversation_premium_used=0,
        user_message="complex order issue",
        routing_enabled=True,
        max_per_conversation=2,
    )


def test_should_run_classifier_strong_throttle_never() -> None:
    policy = _policy()
    assert not should_run_classifier(
        policy=policy,
        throttle_tier="strong",
        premium_remaining=100,
        conversation_premium_used=0,
        user_message="complex order issue",
        routing_enabled=True,
        max_per_conversation=2,
    )


def test_resolve_turn_model_sync_uses_premium_when_classifier_says_so() -> None:
    policy = _policy()
    decision = resolve_turn_model_sync(
        policy=policy,
        throttle_tier="normal",
        premium_remaining=10,
        conversation_premium_used=0,
        classifier=ClassifierResult(use_premium=True, reason="order_dispute"),
        routing_enabled=True,
    )
    assert decision.model == "gpt-4o"
    assert decision.used_premium is True


def test_resolve_turn_model_sync_stays_cheap_when_no_budget() -> None:
    policy = _policy()
    decision = resolve_turn_model_sync(
        policy=policy,
        throttle_tier="normal",
        premium_remaining=0,
        conversation_premium_used=0,
        classifier=ClassifierResult(use_premium=True, reason="ignored"),
        routing_enabled=True,
    )
    assert decision.model == "gpt-4o-mini"
    assert decision.used_premium is False


def test_apply_usage_limit_downgrade_on_strong() -> None:
    out = _apply_usage_limit_model_downgrade(
        throttle_tier="strong",
        model="gpt-4o",
    )
    assert out == "gpt-4o-mini"


def test_advanced_resolution_band() -> None:
    assert advanced_resolution_band(
        plan_slug="hobby",
        included_premium_turns=0,
        premium_turns_used=0,
    ) == "standard_only"
    assert advanced_resolution_band(
        plan_slug="standard",
        included_premium_turns=250,
        premium_turns_used=240,
    ) == "limited"
    assert advanced_resolution_band(
        plan_slug="standard",
        included_premium_turns=250,
        premium_turns_used=250,
    ) == "standard_only"
