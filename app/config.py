"""
Application Configuration - Pydantic Settings for type-safe config.

NO DICTIONARIES - All configuration is strongly typed.
FAIL FAST - Critical config is validated at startup.
"""

import sys

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(Exception):
    """Raised when critical configuration is missing or invalid."""

    pass


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database Configuration - NO DEFAULT for production safety
    database_url: str = ""
    database_read_url: str | None = None  # Optional read replica
    database_pool_size: int = 25
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 3600

    # Environment (accepts ENV or ENVIRONMENT)
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENV", "ENVIRONMENT", "environment"),
    )

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_title: str = "CIRIS Billing API"
    api_version: str = "0.1.0"
    api_description: str = "Credit gating service for CIRIS Agent"

    # Security
    api_key: str | None = None  # Future: API key authentication

    # Admin Authentication - Google OAuth
    GOOGLE_CLIENT_ID: str = ""  # Google OAuth client ID (web client)
    GOOGLE_CLIENT_IDS: str = ""  # Comma-separated list of valid client IDs (web + Android)
    GOOGLE_CLIENT_SECRET: str = ""  # Google OAuth client secret
    ADMIN_JWT_SECRET: str = ""  # JWT secret for admin tokens (generate with: openssl rand -hex 32)

    # Test Authentication (for automated testing only - NEVER enable in production)
    CIRIS_TEST_AUTH_ENABLED: bool = False
    CIRIS_TEST_AUTH_TOKEN: str = ""
    CIRIS_TEST_USER_ID: str = "test-user-automated"

    # Apple Sign-In Configuration
    APPLE_CLIENT_ID: str = ""  # iOS app bundle ID (e.g., com.ciris.agent)
    APPLE_CLIENT_IDS: str = ""  # Comma-separated list of valid bundle IDs
    APPLE_TEAM_ID: str = ""  # Apple Developer Team ID

    # Apple StoreKit (App Store Server API)
    APPLE_STOREKIT_KEY_ID: str = ""  # Key ID from App Store Connect
    APPLE_STOREKIT_ISSUER_ID: str = ""  # Issuer ID from App Store Connect
    APPLE_STOREKIT_PRIVATE_KEY: str = ""  # Private key (.p8 file contents, base64 encoded)
    APPLE_STOREKIT_BUNDLE_ID: str = ""  # Bundle ID for purchase validation
    APPLE_STOREKIT_ENVIRONMENT: str = "production"  # "production" or "sandbox"

    @property
    def valid_apple_client_ids(self) -> list[str]:
        """Get list of valid Apple bundle IDs for token validation."""
        ids = []
        if self.APPLE_CLIENT_ID:
            ids.append(self.APPLE_CLIENT_ID)
        if self.APPLE_CLIENT_IDS:
            for cid in self.APPLE_CLIENT_IDS.split(","):
                cid = cid.strip()
                if cid and cid not in ids:
                    ids.append(cid)
        return ids

    @property
    def valid_google_client_ids(self) -> list[str]:
        """Get list of valid Google client IDs for token validation."""
        ids = []
        # Add primary client ID
        if self.GOOGLE_CLIENT_ID:
            ids.append(self.GOOGLE_CLIENT_ID)
        # Add additional client IDs from comma-separated list
        if self.GOOGLE_CLIENT_IDS:
            for cid in self.GOOGLE_CLIENT_IDS.split(","):
                cid = cid.strip()
                if cid and cid not in ids:
                    ids.append(cid)
        return ids

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    # Observability - Metrics
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # Observability - Tracing (disabled by default - enable when otel-collector is deployed)
    tracing_enabled: bool = False
    otlp_endpoint: str = "http://otel-collector:4317"
    otlp_insecure: bool = True
    service_name: str = "ciris-billing-api"

    # Observability - Sampling
    trace_sample_rate: float = 1.0  # 1.0 means 100% sampling

    # Observability - CIRISLens Log Shipping
    cirislens_token: str = ""  # Service token from CIRISLens admin
    cirislens_endpoint: str = "https://agents.ciris.ai/lens/api/v1/logs/ingest"

    # Payment Provider - Stripe
    stripe_api_key: str = ""  # Stripe secret key (sk_test_... or sk_live_...)
    stripe_webhook_secret: str = ""  # Stripe webhook signing secret (whsec_...)
    stripe_publishable_key: str = ""  # Stripe publishable key (pk_test_... or pk_live_...)

    # Play Integrity API
    # Service account JSON for calling Play Integrity API (base64 encoded or raw JSON)
    PLAY_INTEGRITY_SERVICE_ACCOUNT: str = ""
    # Android package name for integrity verification
    ANDROID_PACKAGE_NAME: str = ""  # e.g., "ai.ciris.agent"

    # Pricing Configuration (LLM usage - legacy)
    free_uses_per_account: int = 10  # Free interactions for new users
    paid_uses_per_purchase: int = 20
    price_per_purchase_minor: int = 500  # $5.00 in cents

    # Product Configuration - Tool Credits
    # Web Search (Brave API)
    product_web_search_free_initial: int = 10  # Free searches on account creation
    product_web_search_free_daily: int = 3  # Free searches refreshed daily
    product_web_search_price_minor: int = 10  # 10 cents ($0.10) per search - standard pricing

    # Future products can be added here:
    # product_image_gen_free_initial: int = 5
    # product_image_gen_free_daily: int = 0
    # product_image_gen_price_minor: int = 10  # 10 cents per image

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def validate_critical_config(self) -> "Settings":
        """
        FAIL FAST: Validate critical configuration at startup.

        The app MUST NOT start if critical config is missing.
        This prevents silent failures that only manifest at runtime.
        """
        errors: list[str] = []

        # DATABASE_URL is absolutely required
        if not self.database_url:
            errors.append("DATABASE_URL is required but empty or missing")
        elif not self.database_url.startswith(("postgresql", "postgres")):
            errors.append(
                f"DATABASE_URL must be a PostgreSQL URL, got: {self.database_url[:20]}..."
            )

        # Validate test auth configuration
        if self.CIRIS_TEST_AUTH_ENABLED:
            if self.environment.lower() == "production":
                errors.append("CIRIS_TEST_AUTH_ENABLED must not be True in production")
            if len(self.CIRIS_TEST_AUTH_TOKEN) < 64:
                errors.append(
                    "CIRIS_TEST_AUTH_TOKEN must be at least 64 characters when test auth is enabled"
                )

        # If we have errors, fail immediately with clear messaging
        if errors:
            error_msg = "\n".join(
                [
                    "",
                    "=" * 60,
                    "CRITICAL CONFIGURATION ERROR - APPLICATION CANNOT START",
                    "=" * 60,
                    *[f"  ✗ {e}" for e in errors],
                    "=" * 60,
                    "",
                ]
            )
            print(error_msg, file=sys.stderr)
            raise ConfigurationError(error_msg)

        return self

    @property
    def read_database_url(self) -> str:
        """Get read database URL (fallback to primary if no replica)."""
        return self.database_read_url or self.database_url


# Global settings instance - validates at import time
settings = Settings()


def get_settings() -> Settings:
    """Get application settings instance."""
    return settings
