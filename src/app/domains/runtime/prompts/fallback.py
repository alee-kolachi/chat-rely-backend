"""Fallback copy helpers (escalation-aware)."""


def excerpt_fallback_instruction(
    fallback_message: str,
    *,
    escalation_enabled: bool,
) -> str:
    fb = (fallback_message or "").strip()
    if escalation_enabled and fb:
        return (
            "If those excerpts do not contain a concrete answer, use this fallback message:\n"
            f"{fb}"
        )
    return (
        "Only if the excerpts truly omit the fact the customer asked for, say briefly what you cannot "
        "confirm from the index. Do not mention escalating to a human, live agents, or handoff — "
        "that is not available in this chat."
    )


def shopify_supplement_fallback_instruction(
    fallback_message: str,
    *,
    escalation_enabled: bool,
) -> str:
    fb = (fallback_message or "").strip()
    if escalation_enabled and fb:
        return (
            "Use the fallback message below **only** when neither the tools (after you call them) nor the "
            f"excerpts support a concrete answer, or the question is general chitchat.\n{fb}"
        )
    return (
        "Use the fallback message below **only** when neither the tools (after you call them) nor the "
        "excerpts support a concrete answer, or the question is general chitchat — but **do not** mention "
        "human escalation or handoff; suggest the website or official contact channels instead.\n"
        f"{fb}"
        if fb
        else "If tools and excerpts do not support a concrete answer, say what you cannot confirm and "
        "point to the website or official contact channels. Do not mention human escalation."
    )
