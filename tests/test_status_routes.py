"""Tests for status API routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.status_routes import (
    ProviderStatus,
    StatusLevel,
    calculate_overall_status,
    check_google_oauth,
    check_google_play,
    check_postgresql,
)


class TestStatusLevels:
    """Tests for status level calculations."""

    def test_all_operational_returns_operational(self) -> None:
        """Overall status is operational when all providers are operational."""
        providers = {
            "postgresql": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=10,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_oauth": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=50,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_play": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=80,
                last_check="2025-12-14T00:00:00Z",
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OPERATIONAL

    def test_one_degraded_returns_degraded(self) -> None:
        """Overall status is degraded when any provider is degraded."""
        providers = {
            "postgresql": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=10,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_oauth": ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=1500,
                last_check="2025-12-14T00:00:00Z",
                message="High latency",
            ),
            "google_play": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=80,
                last_check="2025-12-14T00:00:00Z",
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.DEGRADED

    def test_one_outage_returns_outage(self) -> None:
        """Overall status is outage when any provider is in outage."""
        providers = {
            "postgresql": ProviderStatus(
                status=StatusLevel.OUTAGE,
                latency_ms=None,
                last_check="2025-12-14T00:00:00Z",
                message="Connection failed",
            ),
            "google_oauth": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=50,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_play": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=80,
                last_check="2025-12-14T00:00:00Z",
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OUTAGE

    def test_outage_takes_precedence_over_degraded(self) -> None:
        """Outage status takes precedence over degraded."""
        providers = {
            "postgresql": ProviderStatus(
                status=StatusLevel.OUTAGE,
                latency_ms=None,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_oauth": ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=1500,
                last_check="2025-12-14T00:00:00Z",
            ),
            "google_play": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=80,
                last_check="2025-12-14T00:00:00Z",
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OUTAGE


class TestPostgresqlCheck:
    """Tests for PostgreSQL health check."""

    @pytest.mark.asyncio
    async def test_postgresql_operational(self) -> None:
        """PostgreSQL check returns operational on successful query."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        async def mock_get_write_db():
            yield mock_db

        with patch("app.api.status_routes.get_write_db", mock_get_write_db):
            result = await check_postgresql()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_postgresql_outage_on_exception(self) -> None:
        """PostgreSQL check returns outage on connection failure."""

        async def mock_get_write_db():
            raise Exception("Connection refused")
            yield  # Never reached, but needed for generator

        with patch("app.api.status_routes.get_write_db", mock_get_write_db):
            result = await check_postgresql()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Connection failed"


class TestGoogleOAuthCheck:
    """Tests for Google OAuth health check."""

    @pytest.mark.asyncio
    async def test_google_oauth_operational(self) -> None:
        """Google OAuth check returns operational on 400 response (expected)."""
        mock_response = MagicMock()
        mock_response.status_code = 400  # Expected - no token provided

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_google_oauth()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_google_oauth_outage_on_timeout(self) -> None:
        """Google OAuth check returns outage on timeout."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_google_oauth()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Timeout"

    @pytest.mark.asyncio
    async def test_google_oauth_outage_on_connection_error(self) -> None:
        """Google OAuth check returns outage on connection error."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=httpx.ConnectError("Failed"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            result = await check_google_oauth()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Connection failed"


class TestGooglePlayCheck:
    """Tests for Google Play health check."""

    @pytest.mark.asyncio
    async def test_google_play_not_configured(self) -> None:
        """Google Play check returns operational when not configured."""
        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = ""

            result = await check_google_play()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.message == "Not configured"
        assert result.latency_ms == 0

    @pytest.mark.asyncio
    async def test_google_play_operational(self) -> None:
        """Google Play check returns operational on 200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = "some-config"

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.get = AsyncMock(return_value=mock_response)
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                result = await check_google_play()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_google_play_outage_on_timeout(self) -> None:
        """Google Play check returns outage on timeout."""
        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = "some-config"

            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_client.return_value = mock_instance

                result = await check_google_play()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Timeout"


class TestProviderStatusModel:
    """Tests for ProviderStatus model."""

    def test_provider_status_with_all_fields(self) -> None:
        """ProviderStatus accepts all fields."""
        status = ProviderStatus(
            status=StatusLevel.DEGRADED,
            latency_ms=1500,
            last_check="2025-12-14T00:00:00Z",
            message="High latency",
        )
        assert status.status == StatusLevel.DEGRADED
        assert status.latency_ms == 1500
        assert status.message == "High latency"

    def test_provider_status_optional_fields(self) -> None:
        """ProviderStatus works with optional fields."""
        status = ProviderStatus(
            status=StatusLevel.OUTAGE,
            last_check="2025-12-14T00:00:00Z",
        )
        assert status.status == StatusLevel.OUTAGE
        assert status.latency_ms is None
        assert status.message is None


class TestStatusCaching:
    """Tests for status endpoint caching (DoS protection)."""

    def test_cache_ttl_constant_is_reasonable(self) -> None:
        """Cache TTL should be between 5 and 60 seconds."""
        from app.api.status_routes import _CACHE_TTL_SECONDS

        assert 5 <= _CACHE_TTL_SECONDS <= 60, "Cache TTL should be 5-60 seconds"

    def test_cache_is_initialized_empty(self) -> None:
        """Status cache should start empty or be clearable."""
        import app.api.status_routes as status_module

        # Clear cache for test isolation
        status_module._status_cache.clear()
        assert len(status_module._status_cache) == 0
