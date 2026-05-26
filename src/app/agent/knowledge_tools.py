"""Knowledge-base search tool for the chat agent (on-demand RAG)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.db.session import get_session_factory
from app.domains.runtime.service import (
    RAG_PROMPT_CHUNK_COUNT,
    _build_context_block,
    _retrieve_merged_chunks_for_message,
)

SEARCH_KNOWLEDGE_BASE_TOOL_NAME = "search_knowledge_base"

KNOWLEDGE_TOOL_STATUS = "Searching our site and help content…"


def is_knowledge_tool_name(name: str) -> bool:
    return (name or "").strip() == SEARCH_KNOWLEDGE_BASE_TOOL_NAME


class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "What to look up in the indexed knowledge base: product categories, policies, "
            "FAQs, shipping, returns, or other static site content."
        ),
    )


def build_search_knowledge_base_tool(
    *,
    agent_id: UUID,
    min_similarity: float,
) -> StructuredTool:
    """LangChain tool: vector search over indexed knowledge for this agent."""

    async def _search_knowledge_base(query: str) -> str:
        q = (query or "").strip()
        if not q:
            return json.dumps({"excerpts": "", "chunk_count": 0})

        async with get_session_factory()() as db:
            chunks, _billing = await _retrieve_merged_chunks_for_message(
                db,
                agent_id,
                user_message=q,
                expanded_query=q,
                min_similarity=min_similarity,
                match_count=10,
            )
        block = _build_context_block(chunks[:RAG_PROMPT_CHUNK_COUNT], user_message=q)
        return json.dumps(
            {
                "excerpts": block,
                "chunk_count": len(chunks),
            }
        )

    return StructuredTool.from_function(
        coroutine=_search_knowledge_base,
        name=SEARCH_KNOWLEDGE_BASE_TOOL_NAME,
        description=(
            "Search the brand's indexed website and knowledge sources for policies, FAQs, "
            "product categories, collections, and static marketing copy. "
            "Call this when the shopper asks about what the store sells, product types, "
            "policies, or site content — not for live stock, orders, or prices (use Shopify tools). "
            "Do not call for greetings, thanks, or chitchat."
        ),
        args_schema=SearchKnowledgeBaseInput,
    )


def knowledge_tool_status_message(tool_name: str) -> str:
    _ = tool_name
    return KNOWLEDGE_TOOL_STATUS
