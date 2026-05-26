import { Container, getContainer } from "@cloudflare/containers";

/** Env vars forwarded from Worker secrets/vars into the FastAPI container process. */
const CONTAINER_ENV_KEYS = [
  "APP_ENV",
  "APP_VERSION",
  "LOG_LEVEL",
  "LOG_FILE_ENABLED",
  "LOG_PRETTY_FILE_ENABLED",
  "DATABASE_URL",
  "SUPABASE_JWKS_URL",
  "SUPABASE_ISSUER",
  "SUPABASE_AUDIENCE",
  "OPENAI_API_KEY",
  "OPENAI_EMBEDDING_MODEL",
  "OPENAI_CHAT_MODEL",
  "RUNTIME_USAGE_LIMIT_EXCEEDED_MODEL",
  "DEV_AUTH_BYPASS_ENABLED",
  "DEV_AUTH_BYPASS_USER_ID",
  "PUBLIC_API_BASE_URL",
  "SHOPIFY_API_KEY",
  "SHOPIFY_API_SECRET",
  "SHOPIFY_SCOPES",
  "SHOPIFY_API_VERSION",
  "SHOPIFY_OAUTH_SUCCESS_REDIRECT",
  "INTEGRATION_TOKEN_FERNET_KEY",
  "INTEGRATION_OAUTH_STATE_SECRET",
  "STRIPE_SECRET_KEY",
  "STRIPE_PUBLISHABLE_KEY",
  "STRIPE_WEBHOOK_SECRET",
  "STRIPE_PRICE_HOBBY_MONTHLY",
  "STRIPE_PRICE_STANDARD_MONTHLY",
  "STRIPE_PRICE_PRO_MONTHLY",
  "STRIPE_PRICE_SCALE_MONTHLY",
  "STRIPE_PRICE_STARTER_MONTHLY",
  "STRIPE_PRICE_GROWTH_MONTHLY",
  "BILLING_APP_BASE_URL",
  "BILLING_APP_EXTRA_ORIGINS",
  "BILLING_INTERNAL_SECRET",
  "ADMIN_EMAILS",
  "ALLOWED_ORIGINS",
  "MAILJET_API_KEY",
  "MAILJET_API_SECRET",
  "MAILJET_SENDER_EMAIL",
  "MAILJET_SENDER_NAME",
  "MAILJET_INBOUND_DOMAIN",
  "MAILJET_REPLY_HMAC_SECRET",
  "MAILJET_INBOUND_WEBHOOK_SECRET",
  "LLM_INPUT_PRICE_PER_MILLION_USD",
  "LLM_OUTPUT_PRICE_PER_MILLION_USD",
  "EMBEDDING_PRICE_PER_MILLION_USD",
] as const;

function containerEnvFromBindings(workerEnv: Env): Record<string, string> {
  const record = workerEnv as unknown as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const key of CONTAINER_ENV_KEYS) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) {
      out[key] = value;
    }
  }
  return out;
}

/** Durable Object + container gateway for the FastAPI app. Class name must match wrangler `class_name`. */
export class BackendContainer extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = "30m";

  override onStart(): void {
    this.envVars = containerEnvFromBindings(this.env);
    console.log("BackendContainer started");
  }

  override onError(error: unknown): void {
    console.error("BackendContainer error:", error);
  }
}

export interface Env {
  BACKEND: DurableObjectNamespace<BackendContainer>;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const container = getContainer(env.BACKEND, "api");
    return container.fetch(request);
  },
};
