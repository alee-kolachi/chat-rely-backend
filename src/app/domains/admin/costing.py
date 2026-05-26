"""Cost-calculation primitives for the admin panel.

All pricing comes from env-driven JSON maps on `Settings` (see `core/settings.py`):

  - `llm_input_price_per_million_usd`    (per-million tokens)
  - `llm_output_price_per_million_usd`   (per-million tokens)
  - `embedding_price_per_million_usd`    (per-million tokens)

Model identifiers used as keys are validated by `Settings.parse_price_map` against
`^[A-Za-z0-9._\\-:]+$`, so they are safe to interpolate directly into a dynamic SQL
CASE expression. Float values are formatted with `repr()` which always emits a
decimal-only representation for floats (no thousand separators, no locale issues).

A model present in `messages.model` but absent from the maps yields NULL in the SQL
expression and `None` in the Python helper — surface these via `unknown_models_warning`
so the UI can render `—` and prompt the operator to add the missing prices.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.settings import Settings


_KEY_RE = re.compile(r"^[A-Za-z0-9._\-:]+$")


def _format_price(value: float) -> str:
    """Render a price as a SQL-safe numeric literal.

    `repr(float)` is always a plain decimal in C locale (e.g. `0.15`, `2.5`, `10.0`),
    never `1.5e-2` for normal pricing magnitudes — safe to inline.
    """
    return repr(float(value))


def _validate_keys(prices: dict[str, float]) -> None:
    """Defense in depth — Settings already validates these, but services may build maps too."""
    for k in prices:
        if not _KEY_RE.fullmatch(k):
            raise ValueError(f"invalid price-map key {k!r} (must match {_KEY_RE.pattern})")


def build_llm_cost_usd_expr(
    in_prices: dict[str, float],
    out_prices: dict[str, float],
    *,
    alias: str = "m",
    model_column: str = "model",
    in_tokens_column: str = "input_tokens",
    out_tokens_column: str = "output_tokens",
) -> str:
    """Build a per-row USD cost SQL expression for the messages table.

    Returns NULL when the row's model is missing from the maps so callers can detect
    "unpriced" rows separately from genuine $0 rows. Aggregate callers should `coalesce`
    to 0.0 when they want to keep summing across mixed-pricing data.

    Example output (formatted for readability):

        (CASE
           WHEN m.model = 'gpt-4o-mini' THEN m.input_tokens * 0.15 + m.output_tokens * 0.6
           WHEN m.model = 'gpt-4o'      THEN m.input_tokens * 2.5  + m.output_tokens * 10.0
           ELSE NULL
         END) / 1000000.0
    """
    _validate_keys(in_prices)
    _validate_keys(out_prices)

    models = sorted(set(in_prices) | set(out_prices))
    if not models:
        return "CAST(NULL AS double precision)"

    in_col = f"{alias}.{in_tokens_column}"
    out_col = f"{alias}.{out_tokens_column}"
    model_col = f"{alias}.{model_column}"

    lines = ["(CASE"]
    for model in models:
        in_p = _format_price(in_prices.get(model, 0.0))
        out_p = _format_price(out_prices.get(model, 0.0))
        lines.append(
            f"  WHEN {model_col} = '{model}' THEN "
            f"{in_col} * {in_p} + {out_col} * {out_p}"
        )
    lines.append("  ELSE NULL")
    lines.append("END) / 1000000.0")
    return "\n".join(lines)


def build_embedding_cost_usd_expr(
    embedding_prices: dict[str, float],
    embedding_model: str,
    *,
    alias: str = "kc",
    tokens_column: str = "token_count",
) -> str:
    """Build a per-row USD cost SQL expression for `knowledge_chunks`.

    Embedding chunks don't carry a `model` column — they're all priced at the global
    `openai_embedding_model` setting. If that model isn't priced, the expression yields
    NULL so the UI can flag the gap.
    """
    _validate_keys(embedding_prices)
    if embedding_model not in embedding_prices:
        return "CAST(NULL AS double precision)"
    price = _format_price(embedding_prices[embedding_model])
    return f"({alias}.{tokens_column} * {price}) / 1000000.0"


def compute_embedding_cost_usd(settings: Settings, embedding_tokens: int) -> float | None:
    """USD for embedding API usage at `settings.openai_embedding_model` price (per-million map)."""
    if embedding_tokens <= 0:
        return 0.0
    model = (settings.openai_embedding_model or "").strip()
    if not model:
        return None
    price = settings.embedding_price_per_million_usd.get(model)
    if price is None:
        return None
    return (embedding_tokens * price) / 1_000_000.0


def compute_message_cost_usd(
    settings: Settings,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Python-side cost calculation for a single message.

    Returns None when:
      - `model` is None / empty (e.g. user/system/tool messages with no LLM call), or
      - `model` is not in either price map (unknown pricing).
    """
    if not model:
        return None
    in_price = settings.llm_input_price_per_million_usd.get(model)
    out_price = settings.llm_output_price_per_million_usd.get(model)
    if in_price is None and out_price is None:
        return None
    in_p = in_price or 0.0
    out_p = out_price or 0.0
    return (input_tokens * in_p + output_tokens * out_p) / 1_000_000.0


def unknown_models_warning(
    observed_models: Iterable[str | None],
    settings: Settings,
) -> list[str]:
    """Return models that show up in messages but aren't in either LLM price map.

    Embedding model is checked separately (single-value); see `embedding_pricing_status`.
    """
    in_map = settings.llm_input_price_per_million_usd
    out_map = settings.llm_output_price_per_million_usd
    seen: set[str] = set()
    for m in observed_models:
        if not m:
            continue
        if m not in in_map and m not in out_map:
            seen.add(m)
    return sorted(seen)


def embedding_pricing_status(settings: Settings) -> tuple[bool, str]:
    """Returns (priced, embedding_model). Used by the costing overview to flag a gap."""
    model = settings.openai_embedding_model
    return (model in settings.embedding_price_per_million_usd, model)
