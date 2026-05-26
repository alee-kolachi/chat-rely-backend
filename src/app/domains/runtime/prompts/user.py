from app.domains.runtime.prompts.fallback import (
    excerpt_fallback_instruction,
    shopify_supplement_fallback_instruction,
)

_EXCERPT_ANSWER_RULES = (
    "ANSWER FROM EXCERPTS\n"
    "- Lead with specific facts from the excerpts (product types, styles, features, policies).\n"
    "- When the excerpts list styles, designs, or categories, quote them in bullets. "
    "Do not replace them with vague phrases like 'versatility and timeless style' unless those words "
    "directly answer the question.\n"
    "- Do not say the excerpts lack detail when they name concrete types or features.\n"
    "- Do not tell the customer to visit the website, check the website, or contact customer service "
    "when the excerpts already answer their question.\n"
)


def build_grounded_user_prompt(
    context_block: str,
    fallback_message: str,
    user_message: str,
    *,
    shopify_tools_enabled: bool = False,
    escalation_enabled: bool = False,
) -> str:
    if shopify_tools_enabled:
        fb_line = shopify_supplement_fallback_instruction(
            fallback_message,
            escalation_enabled=escalation_enabled,
        )
        return (
            f"The following excerpts are **supplementary** context from the brand’s knowledge index. "
            f"They may be irrelevant or incomplete for this question.\n\n"
            f"{context_block}\n\n"
            f"If the customer needs **live store data** (order status, tracking, inventory, catalog SKUs/prices, "
            f"or purchase history for this merchant), you **must** use the enabled Shopify tools first—those facts "
            f"are not in the excerpts.\n"
            f"{fb_line}\n\n"
            f"Customer message:\n{user_message}"
        )
    fb_line = excerpt_fallback_instruction(
        fallback_message,
        escalation_enabled=escalation_enabled,
    )
    return (
        f"The following excerpts are the best-matching passages from the brand’s knowledge index. "
        f"Answer the customer’s question using them.\n\n"
        f"{_EXCERPT_ANSWER_RULES}\n\n"
        f"{context_block}\n\n"
        f"{fb_line}\n\n"
        f"Customer message:\n{user_message}"
    )
