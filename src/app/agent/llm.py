"""OpenAI chat model factory."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.core.errors import AppError
from app.core.settings import get_settings


def make_chat_model(model: str, *, temperature: float = 0.0) -> ChatOpenAI:
    settings = get_settings()
    if not settings.openai_api_key:
        raise AppError(
            code="runtime.llm_not_configured",
            message="OPENAI_API_KEY is required for runtime chat",
            status_code=500,
        )
    t = max(0.0, min(1.0, float(temperature)))
    return ChatOpenAI(
        model=model,
        temperature=t,
        api_key=settings.openai_api_key,
        timeout=15,
        max_retries=0,
        max_tokens=256,
        streaming=True,
    )
