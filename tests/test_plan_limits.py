"""Canonical plan limit helpers (conversations / agents / actions / training)."""

from app.domains.plans.plan_limits import (
    analytics_access_tier_for_plan_slug,
    message_feedback_enabled_for_plan_slug,
    plan_limits_dto_from_row,
    sources_suggestions_enabled_for_plan_slug,
    training_storage_cap_bytes,
    website_crawl_cap_bytes,
)


def test_training_storage_min_of_mb_and_kb() -> None:
    assert training_storage_cap_bytes({"max_total_knowledge_mb": 1, "max_knowledge_storage_kb": 500}) == 500 * 1024
    assert training_storage_cap_bytes({"max_total_knowledge_mb": 15}) == 15 * 1024 * 1024
    assert training_storage_cap_bytes({"max_knowledge_storage_kb": 500}) == 500 * 1024


def test_website_crawl_cap_respects_kb_and_storage() -> None:
    storage = 500 * 1024
    assert website_crawl_cap_bytes({"max_website_crawl_kb": 500}, storage) == storage
    big = 50 * 1024 * 1024
    assert website_crawl_cap_bytes({}, big) == big
    assert website_crawl_cap_bytes({"max_website_crawl_kb": 100}, big) == 100 * 1024


def test_plan_limits_dto_from_row() -> None:
    lim = plan_limits_dto_from_row(
        included_conversations=200,
        max_agents=2,
        features={
            "max_enabled_actions_per_agent": 5,
            "max_total_knowledge_mb": 40,
        },
    )
    assert lim.included_conversations == 200
    assert lim.max_agents == 2
    assert lim.max_enabled_actions_per_agent == 5
    assert lim.max_total_knowledge_bytes == 40 * 1024 * 1024
    assert lim.max_website_crawl_bytes == 40 * 1024 * 1024


def test_sources_suggestions_enabled_for_plan_slug() -> None:
    assert sources_suggestions_enabled_for_plan_slug("standard") is True
    assert sources_suggestions_enabled_for_plan_slug("pro") is True
    assert sources_suggestions_enabled_for_plan_slug("free") is False
    assert sources_suggestions_enabled_for_plan_slug("hobby") is False
    assert sources_suggestions_enabled_for_plan_slug("scale") is True
    assert sources_suggestions_enabled_for_plan_slug(None) is False


def test_message_feedback_enabled_for_plan_slug() -> None:
    assert message_feedback_enabled_for_plan_slug("pro") is True
    assert message_feedback_enabled_for_plan_slug("scale") is True
    assert message_feedback_enabled_for_plan_slug("standard") is False
    assert message_feedback_enabled_for_plan_slug("hobby") is False
    assert message_feedback_enabled_for_plan_slug("free") is False
    assert message_feedback_enabled_for_plan_slug(None) is False


def test_analytics_access_tier_for_plan_slug() -> None:
    assert analytics_access_tier_for_plan_slug("free") == "none"
    assert analytics_access_tier_for_plan_slug(None) == "none"
    assert analytics_access_tier_for_plan_slug("") == "none"
    assert analytics_access_tier_for_plan_slug("unknown") == "none"
    assert analytics_access_tier_for_plan_slug("hobby") == "basic"
    assert analytics_access_tier_for_plan_slug("standard") == "full"
    assert analytics_access_tier_for_plan_slug("pro") == "full"
    assert analytics_access_tier_for_plan_slug("scale") == "full"
