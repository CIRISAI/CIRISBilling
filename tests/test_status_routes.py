"""
Tests for Status API Routes.

Tests health check endpoints and provider status checks.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.status_routes import (
    ProviderStatus,
    ServiceStatusResponse,
    StatusLevel,
    calculate_overall_status,
    check_google_oauth,
    check_google_play,
    check_postgresql,
)


class TestStatusLevel:
    """Tests for StatusLevel enum."""

    def test_status_levels_exist(self):
        """StatusLevel has expected values."""
        assert StatusLevel.OPERATIONAL == "operational"
        assert StatusLevel.DEGRADED == "degraded"
        assert StatusLevel.OUTAGE == "outage"


class TestProviderStatusModel:
    """Tests for ProviderStatus model."""

    def test_provider_status_operational(self):
        """ProviderStatus with operational status."""
        status = ProviderStatus(
            status=StatusLevel.OPERATIONAL,
            latency_ms=50,
            last_check=datetime.now(UTC).isoformat(),
            message=None,
        )
        assert status.status == StatusLevel.OPERATIONAL
        assert status.latency_ms == 50

    def test_provider_status_degraded(self):
        """ProviderStatus with degraded status."""
        status = ProviderStatus(
            status=StatusLevel.DEGRADED,
            latency_ms=1500,
            last_check=datetime.now(UTC).isoformat(),
            message="High latency",
        )
        assert status.status == StatusLevel.DEGRADED
        assert status.message == "High latency"

    def test_provider_status_outage(self):
        """ProviderStatus with outage status."""
        status = ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=None,
            last_check=datetime.now(UTC).isoformat(),
            message="Connection failed",
        )
        assert status.status == StatusLevel.OUTAGE
        assert status.latency_ms is None


class TestCalculateOverallStatus:
    """Tests for calculate_overall_status function."""

    def test_all_operational(self):
        """All providers operational returns operational."""
        providers = {
            "db": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=50,
                last_check=datetime.now(UTC).isoformat(),
            ),
            "oauth": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=100,
                last_check=datetime.now(UTC).isoformat(),
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OPERATIONAL

    def test_one_degraded(self):
        """One degraded provider returns degraded."""
        providers = {
            "db": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=50,
                last_check=datetime.now(UTC).isoformat(),
            ),
            "oauth": ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=1500,
                last_check=datetime.now(UTC).isoformat(),
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.DEGRADED

    def test_one_outage(self):
        """One outage returns outage."""
        providers = {
            "db": ProviderStatus(
                status=StatusLevel.OUTAGE,
                latency_ms=None,
                last_check=datetime.now(UTC).isoformat(),
            ),
            "oauth": ProviderStatus(
                status=StatusLevel.OPERATIONAL,
                latency_ms=100,
                last_check=datetime.now(UTC).isoformat(),
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OUTAGE

    def test_outage_takes_priority_over_degraded(self):
        """Outage status takes priority over degraded."""
        providers = {
            "db": ProviderStatus(
                status=StatusLevel.OUTAGE,
                latency_ms=None,
                last_check=datetime.now(UTC).isoformat(),
            ),
            "oauth": ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=1500,
                last_check=datetime.now(UTC).isoformat(),
            ),
        }
        assert calculate_overall_status(providers) == StatusLevel.OUTAGE


class TestCheckPostgresql:
    """Tests for check_postgresql function."""

    @pytest.mark.asyncio
    async def test_postgresql_operational(self):
        """PostgreSQL check returns operational on success."""
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
    async def test_postgresql_outage_on_error(self):
        """PostgreSQL check returns outage on connection error."""

        async def mock_get_write_db():
            raise ConnectionError("Cannot connect")
            yield  # noqa: unreachable

        with patch("app.api.status_routes.get_write_db", mock_get_write_db):
            result = await check_postgresql()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Connection failed"


class TestCheckGoogleOAuth:
    """Tests for check_google_oauth function."""

    @pytest.mark.asyncio
    async def test_google_oauth_operational(self):
        """Google OAuth check returns operational on 400 response."""
        mock_response = MagicMock()
        mock_response.status_code = 400  # Expected when no token provided

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = await check_google_oauth()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_google_oauth_timeout(self):
        """Google OAuth check returns outage on timeout."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = await check_google_oauth()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Timeout"

    @pytest.mark.asyncio
    async def test_google_oauth_connection_error(self):
        """Google OAuth check returns outage on connection error."""
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = await check_google_oauth()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Connection failed"

    @pytest.mark.asyncio
    async def test_google_oauth_unexpected_status(self):
        """Google OAuth check returns degraded on unexpected status code."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            result = await check_google_oauth()

        assert result.status == StatusLevel.DEGRADED
        assert "Unexpected status" in result.message


class TestCheckGooglePlay:
    """Tests for check_google_play function."""

    @pytest.mark.asyncio
    async def test_google_play_not_configured(self):
        """Google Play check returns operational if not configured."""
        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = None

            result = await check_google_play()

        assert result.status == StatusLevel.OPERATIONAL
        assert result.message == "Not configured"
        assert result.latency_ms == 0

    @pytest.mark.asyncio
    async def test_google_play_operational(self):
        """Google Play check returns operational on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = "service-account.json"

            with patch("httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                MockClient.return_value = mock_client

                result = await check_google_play()

        assert result.status == StatusLevel.OPERATIONAL

    @pytest.mark.asyncio
    async def test_google_play_timeout(self):
        """Google Play check returns outage on timeout."""
        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = "service-account.json"

            with patch("httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                MockClient.return_value = mock_client

                result = await check_google_play()

        assert result.status == StatusLevel.OUTAGE
        assert result.message == "Timeout"

    @pytest.mark.asyncio
    async def test_google_play_unexpected_status(self):
        """Google Play check returns degraded on unexpected status."""
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("app.api.status_routes.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = "service-account.json"

            with patch("httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                MockClient.return_value = mock_client

                result = await check_google_play()

        assert result.status == StatusLevel.DEGRADED


class TestServiceStatusResponse:
    """Tests for ServiceStatusResponse model."""

    def test_service_status_response_model(self):
        """ServiceStatusResponse has correct structure."""
        response = ServiceStatusResponse(
            service="cirisbilling",
            status=StatusLevel.OPERATIONAL,
            timestamp=datetime.now(UTC).isoformat(),
            version="1.0.0",
            providers={
                "db": ProviderStatus(
                    status=StatusLevel.OPERATIONAL,
                    latency_ms=50,
                    last_check=datetime.now(UTC).isoformat(),
                ),
            },
        )
        assert response.service == "cirisbilling"
        assert response.status == StatusLevel.OPERATIONAL
        assert len(response.providers) == 1
