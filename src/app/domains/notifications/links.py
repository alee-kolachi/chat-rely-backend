"""Dashboard deep links for notification rows."""

from __future__ import annotations

from uuid import UUID


def href_escalation(*, conversation_id: UUID, agent_id: UUID) -> str:
    return f"/conversations?conversation={conversation_id}&agent={agent_id}"


def href_knowledge_source(*, source_type: str, agent_id: UUID, source_id: UUID) -> str:
    base = {
        "website": "/knowledge/website",
        "file": "/knowledge/files",
        "text_snippet": "/knowledge/text-snippet",
        "q_and_a": "/knowledge/q-and-a",
    }.get(source_type, "/knowledge/website")
    return f"{base}?agent={agent_id}&source={source_id}"


def href_usage() -> str:
    return "/usage"
