"""
Application Configuration - Pydantic Settings for type-safe config.

NO DICTIONARIES - All configuration is strongly typed.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database Configuration
    database_url: str = "postgresql+asyncpg://billing_admin:password@localhost:5432/ciris_billing"
    database_read_url: str | None = None  # Optional read replica
    database_pool_size: int = 25
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 3600

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_title: str = "CIRIS Billing API"
    api_version: str = "0.1.0"
    api_description: str = "Credit gating service for CIRIS Agent"

    # Security
    api_key: str | None = None  # Future: API key authentication

    # Admin Authentication - Google OAuth
    GOOGLE_CLIENT_ID: str = ""  # Google OAuth client ID
    GOOGLE_CLIENT_SECRET: str = ""  # Google OAuth client secret
    ADMIN_JWT_SECRET: str = ""  # JWT secret for admin tokens (generate with: openssl rand -hex 32)

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json or console

    # Observability - Metrics
    metrics_enabled: bool = True
    metrics_port: int = 9090

    # Observability - Tracing
    tracing_enabled: bool = True
    otlp_endpoint: str = "http://otel-collector:4317"
    otlp_insecure: bool = True
    service_name: str = "ciris-billing-api"

    # Observability - Sampling
    trace_sample_rate: float = 1.0  # 1.0 = 100% sampling

    # Payment Provider - Stripe
    stripe_api_key: str = ""  # Stripe secret key (sk_test_... or sk_live_...)
    stripe_webhook_secret: str = ""  # Stripe webhook signing secret (whsec_...)
    stripe_publishable_key: str = ""  # Stripe publishable key (pk_test_... or pk_live_...)

    # Pricing Configuration
    free_uses_per_account: int = 10  # Free interactions for new users
    paid_uses_per_purchase: int = 20
    price_per_purchase_minor: int = 500  # $5.00 in cents

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def read_database_url(self) -> str:
        """Get read database URL (fallback to primary if no replica)."""
        return self.database_read_url or self.database_url


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings instance."""
    return settings
