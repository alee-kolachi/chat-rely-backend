"""SSE chat streaming contract tests."""

from __future__ import annotations

import json

from app.agent.streaming import format_sse


def test_format_sse() -> None:
    out = format_sse("token", {"text": "Hello"})
    assert out.startswith("event: token\n")
    assert "data: " in out
    assert json.loads(out.split("data: ", 1)[1].strip()) == {"text": "Hello"}
    assert out.endswith("\n\n")
