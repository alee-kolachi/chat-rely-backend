import json
import re
from functools import lru_cache
from typing import Annotated, Any

from pydantic import AnyHttpUrl, PostgresDsn, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


# Strict whitelist for model identifier keys in price-map env vars. Keys are interpolated
# directly into a dynamic SQL CASE expression in `app.domains.admin.costing`, so we reject
# anything that could break out of the model literal (quotes, semicolons, parens, …).
_PRICE_MAP_KEY_RE = re.compile(r"^[A-Za-z0-9._\-:]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ChatRely Backend"
    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_file_enabled: bool = True
    log_file_path: str = "logs/backend.log"
    log_file_max_bytes: int = 10485760
    log_file_backup_count: int = 10
    log_pretty_file_enabled: bool = True
    log_pretty_file_path: str = "logs/backend.pretty.log"
    log_pretty_file_max_bytes: int = 10485760
    log_pretty_file_backup_count: int = 10
    allowed_origins: list[AnyHttpUrl] = []

    database_url: PostgresDsn
    supabase_jwks_url: AnyHttpUrl
    supabase_issuer: AnyHttpUrl
    supabase_audience: str = "authenticated"
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"
    """When conversations used exceed the plan included amount, visitor chat uses this model (subscription is unchanged; no per-conversation overage). Override via env."""
    runtime_usage_limit_exceeded_model: str = "gpt-4o-mini"
    runtime_enable_turn_signals: bool = False
    # Cap prior DB messages sent to the LLM per turn (smaller prompts → faster first token).
    runtime_max_history_messages: int = 6
    # Cache enabled Shopify actions list per agent (avoids repeated subscription + agent_actions work).
    runtime_shopify_actions_cache_ttl_seconds: float = 120.0
    # Skip repeated DB lookups when an agent has no connected Shopify store.
    runtime_shopify_disconnected_cache_ttl_seconds: float = 300.0
    # Do not block the LLM while waiting for usage snapshot refresh (0 = cache-only).
    runtime_usage_refresh_wait_seconds: float = 0.0
    # Reserved for optional RAG wait policies; chat does not block on RAG (see stream_chat).
    runtime_rag_prep_budget_seconds: float = 0.0
    runtime_usage_tier_cache_ttl_seconds: float = 120.0
    runtime_agent_config_cache_ttl_seconds: float = 120.0
    # After boot, preload Shopify connection + actions caches for connected stores (first chat avoids cold miss).
    runtime_shopify_cache_warm_on_startup: bool = True
    runtime_shopify_cache_warm_max_agents: int = 20
    # TTL for cached "agent has indexed knowledge_chunks" probe (structural RAG skip).
    runtime_agent_kb_index_cache_ttl_seconds: float = 600.0
    # Default chat model when agent row has empty/legacy slow label (latency).
    runtime_default_chat_model: str = "gpt-4o-mini"
    runtime_premium_chat_model: str = "gpt-4o"
    runtime_model_routing_enabled: bool = True
    runtime_max_premium_turns_per_conversation: int = 2
    runtime_prefer_fast_chat_model: bool = True
    # SQLAlchemy pool: recycle connections (seconds); use Supabase pooler :6543 in DATABASE_URL.
    database_pool_recycle_seconds: int = 300
    # Hard timeout for each Shopify Admin tool invocation (GraphQL); avoids hung streams.
    shopify_tool_timeout_seconds: float = 12.0
    # Log `runtime.turn_timing` when wall-clock assistant turn exceeds this (milliseconds).
    runtime_turn_latency_warn_ms: int = 3000
    dev_auth_bypass_enabled: bool = False
    dev_auth_bypass_user_id: str = "00000000-0000-0000-0000-000000000001"
    # In development, run the indexing worker inside the API process so website crawls continue after onboarding.
    indexing_worker_embedded_in_dev: bool = True

    # HTTP rate limiting (in-process; use a shared store when running multiple API replicas).
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 120
    rate_limit_chat_per_minute: int = 30
    rate_limit_knowledge_per_minute: int = 10
    rate_limit_knowledge_read_per_minute: int = 120
    rate_limit_admin_per_minute: int = 60
    rate_limit_webhook_per_minute: int = 300
    rate_limit_public_per_minute: int = 60

    # Shopify Partner app + OAuth (https://shopify.dev/docs/apps/auth/oauth)
    shopify_api_key: str | None = None
    shopify_api_secret: str | None = None
    """Comma-separated OAuth scopes; defaults match common read-only storefront support."""
    shopify_scopes: str = "read_customers,read_fulfillments,read_inventory,read_orders,read_products"
    shopify_api_version: str = "2026-04"
    """Public URL of this API for OAuth callback (e.g. http://127.0.0.1:8000). No trailing slash."""
    public_api_base_url: str = "http://127.0.0.1:8000"
    """Where to send the merchant browser after successful OAuth (e.g. http://localhost:3000/actions)."""
    shopify_oauth_success_redirect: str = "http://localhost:3000/actions"
    """Fernet key (urlsafe base64 32 bytes). Encrypts shopify access_token at rest."""
    integration_token_fernet_key: str | None = None
    """HMAC secret for signed OAuth state payloads."""
    integration_oauth_state_secret: str | None = None

    # Mailjet (transactional + Parse inbound). Optional until email bridge is configured.
    mailjet_api_key: str | None = None
    mailjet_api_secret: str | None = None
    mailjet_sender_email: str | None = None
    mailjet_sender_name: str = "Support"
    """Domain receiving inbound mail (Parse route), e.g. support.example.com — used in Reply-To."""
    mailjet_inbound_domain: str | None = None
    """Optional separate secret for reply tokens; defaults to integration_oauth_state_secret."""
    mailjet_reply_hmac_secret: str | None = None
    """Shared secret on inbound webhook URL (?verify=) to reject stray traffic."""
    mailjet_inbound_webhook_secret: str | None = None

    # Stripe (https://stripe.com/docs). Keys optional until billing is used.
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    """Signing secret from Dashboard webhook endpoint or `stripe listen` (whsec_...)."""
    stripe_webhook_secret: str | None = None
    """Recurring Price IDs (price_...) from Stripe Dashboard, not Product IDs (prod_...)."""
    stripe_price_hobby_monthly: str | None = None
    stripe_price_standard_monthly: str | None = None
    stripe_price_pro_monthly: str | None = None
    stripe_price_scale_monthly: str | None = None
    """Legacy env names; used when new names are unset (existing Stripe prices / rollout)."""
    stripe_price_starter_monthly: str | None = None
    stripe_price_growth_monthly: str | None = None
    """Origin for Checkout return URLs, e.g. http://localhost:3000"""
    billing_app_base_url: str = "http://localhost:3000"
    """Optional comma-separated extra origins allowed for Stripe return URLs (production)."""
    billing_app_extra_origins: str | None = None
    """Optional shared secret for POST /api/v1/billing/internal/charge-overage (cron)."""
    billing_internal_secret: str | None = None

    """Comma-separated emails authorized to access /api/v1/admin/* (admin panel). Empty = admin disabled.

    `NoDecode` prevents pydantic-settings from JSON-decoding the env value before our validator runs."""
    admin_emails: Annotated[list[str], NoDecode] = []

    @field_validator("admin_emails", mode="before")
    @classmethod
    def split_admin_emails(cls, v: Any) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [e.strip().lower() for e in v.split(",") if e.strip()]
        if isinstance(v, list):
            return [str(e).strip().lower() for e in v if str(e).strip()]
        return []

    def is_admin_email(self, email: str | None) -> bool:
        return bool(email) and email.lower() in self.admin_emails

    """Per-model token pricing in USD per 1,000,000 tokens. JSON object keyed by `messages.model`.

    Adding a new model is an env edit + restart — no DB migration. Keys must match the literal
    `messages.model` value at write-time. Unknown models surface as `unknown_models` in the admin
    Costing overview rather than silently costing $0.

    `NoDecode` defers parsing to our validator so we get a clear error message on bad JSON / bad
    keys (vs pydantic-settings's opaque SettingsError)."""
    llm_input_price_per_million_usd: Annotated[dict[str, float], NoDecode] = {}
    llm_output_price_per_million_usd: Annotated[dict[str, float], NoDecode] = {}
    """Keyed by embedding model name (currently always `openai_embedding_model` since
    `knowledge_chunks` doesn't store a per-row model)."""
    embedding_price_per_million_usd: Annotated[dict[str, float], NoDecode] = {}

    @field_validator(
        "llm_input_price_per_million_usd",
        "llm_output_price_per_million_usd",
        "embedding_price_per_million_usd",
        mode="before",
    )
    @classmethod
    def parse_price_map(cls, v: Any) -> dict[str, float]:
        if v is None or v == "":
            return {}
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError(f"price map env must be a valid JSON object: {exc.msg}") from exc
        if not isinstance(v, dict):
            raise ValueError("price map must be a JSON object (e.g. {\"gpt-4o-mini\":0.15})")
        out: dict[str, float] = {}
        for k, val in v.items():
            key = str(k)
            if not _PRICE_MAP_KEY_RE.fullmatch(key):
                raise ValueError(
                    f"invalid model key {key!r}: only [A-Za-z0-9._-:] characters are allowed"
                )
            try:
                out[key] = float(val)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"price for {key!r} must be a number, got {val!r}") from exc
        return out

    @model_validator(mode="before")
    @classmethod
    def apply_development_defaults(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        app_env = str(values.get("app_env") or values.get("APP_ENV") or "development").lower()
        if app_env != "development":
            return values

        # Keep local startup ergonomic when .env has not been created yet.
        values.setdefault("database_url", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")
        values.setdefault("supabase_jwks_url", "https://example.com/.well-known/jwks.json")
        values.setdefault("supabase_issuer", "https://example.com/auth/v1")
        # Dev-only: replace in production. Fernet key for encrypting integration tokens.
        values.setdefault(
            "integration_token_fernet_key",
            "0xJVyOJM1vvMiH6NSfvvxCVF5Av363ZALelKvhO4NMg=",
        )
        values.setdefault(
            "integration_oauth_state_secret",
            "dev-only-oauth-state-secret-min-32-characters-long",
        )
        return values

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_settings() -> None:
    try:
        get_settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid backend configuration: {exc}") from exc

