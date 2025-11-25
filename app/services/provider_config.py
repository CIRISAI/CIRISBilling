"""
Provider Configuration Service - Load payment provider configs from database.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.models import ProviderConfig

logger = get_logger(__name__)


class ProviderConfigService:
    """Service for loading provider configurations from database."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize provider config service with database session."""
        self.session = session

    async def get_stripe_config(self) -> dict[str, str] | None:
        """
        Get active Stripe configuration from database.

        Returns:
            Dict with api_key, webhook_secret, publishable_key or None if not configured
        """
        stmt = select(ProviderConfig).where(
            ProviderConfig.provider_type == "stripe",
            ProviderConfig.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        config = result.scalar_one_or_none()

        if config is None:
            logger.warning("stripe_config_not_found")
            return None

        config_data = config.config_data
        logger.info("stripe_config_loaded", has_api_key=bool(config_data.get("api_key")))

        return {
            "api_key": config_data.get("api_key", ""),
            "webhook_secret": config_data.get("webhook_secret", ""),
            "publishable_key": config_data.get("publishable_key", ""),
        }

    async def get_google_play_config(self) -> dict[str, str] | None:
        """
        Get active Google Play configuration from database.

        Returns:
            Dict with service_account_json, package_name or None if not configured
        """
        stmt = select(ProviderConfig).where(
            ProviderConfig.provider_type == "google_play",
            ProviderConfig.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        config = result.scalar_one_or_none()

        if config is None:
            logger.warning("google_play_config_not_found")
            return None

        config_data = config.config_data
        logger.info(
            "google_play_config_loaded",
            has_service_account=bool(config_data.get("service_account_json")),
            package_name=config_data.get("package_name"),
        )

        return {
            "service_account_json": config_data.get("service_account_json", ""),
            "package_name": config_data.get("package_name", ""),
        }
