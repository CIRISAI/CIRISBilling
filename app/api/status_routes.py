"""
Status API routes - Health checks for CIRISBilling dependencies.

Public endpoint (no auth) for status page aggregation by CIRISLens.
Rate limited to prevent abuse.
"""

import asyncio
import time
from datetime import UTC, datetime
from enum import Enum

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text
from structlog import get_logger

from app.config import settings
from app.db.session import get_write_db

logger = get_logger(__name__)
router = APIRouter(tags=["status"])

# Timeout for health checks
CHECK_TIMEOUT = 5.0  # seconds
DEGRADED_LATENCY_THRESHOLD = 1000  # ms

# Rate limiting: cache last result for 10 seconds
_status_cache: dict[str, tuple[datetime, "ServiceStatusResponse"]] = {}
_CACHE_TTL_SECONDS = 10


class StatusLevel(str, Enum):
    """Status levels for health checks."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    OUTAGE = "outage"


class ProviderStatus(BaseModel):
    """Status of a single provider."""

    status: StatusLevel
    latency_ms: int | None = None
    last_check: str = Field(..., description="ISO 8601 timestamp")
    message: str | None = None


class ServiceStatusResponse(BaseModel):
    """Response for /v1/status endpoint."""

    service: str = "cirisbilling"
    status: StatusLevel
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    version: str
    providers: dict[str, ProviderStatus]


async def check_postgresql() -> ProviderStatus:
    """Check PostgreSQL connectivity."""
    start = time.perf_counter()
    timestamp = datetime.now(UTC).isoformat()

    try:
        async for db in get_write_db():
            await db.execute(text("SELECT 1"))
            latency_ms = int((time.perf_counter() - start) * 1000)

            status = (
                StatusLevel.DEGRADED
                if latency_ms > DEGRADED_LATENCY_THRESHOLD
                else StatusLevel.OPERATIONAL
            )

            return ProviderStatus(
                status=status,
                latency_ms=latency_ms,
                last_check=timestamp,
                message="High latency" if status == StatusLevel.DEGRADED else None,
            )
    except Exception as e:
        logger.warning("postgresql_health_check_failed", error=str(e))
        return ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=None,
            last_check=timestamp,
            message="Connection failed",
        )

    # Fallback (shouldn't reach here)
    return ProviderStatus(
        status=StatusLevel.OUTAGE,
        latency_ms=None,
        last_check=timestamp,
        message="Unknown error",
    )


async def check_google_oauth() -> ProviderStatus:
    """Check Google OAuth token endpoint reachability."""
    start = time.perf_counter()
    timestamp = datetime.now(UTC).isoformat()

    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
            # Ping Google's token info endpoint (doesn't require auth)
            response = await client.get("https://oauth2.googleapis.com/tokeninfo")
            latency_ms = int((time.perf_counter() - start) * 1000)

            # 400 is expected (no token provided) - endpoint is reachable
            if response.status_code in (200, 400):
                status = (
                    StatusLevel.DEGRADED
                    if latency_ms > DEGRADED_LATENCY_THRESHOLD
                    else StatusLevel.OPERATIONAL
                )
                return ProviderStatus(
                    status=status,
                    latency_ms=latency_ms,
                    last_check=timestamp,
                    message="High latency" if status == StatusLevel.DEGRADED else None,
                )

            return ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=latency_ms,
                last_check=timestamp,
                message=f"Unexpected status: {response.status_code}",
            )
    except httpx.TimeoutException:
        return ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=int(CHECK_TIMEOUT * 1000),
            last_check=timestamp,
            message="Timeout",
        )
    except Exception as e:
        logger.warning("google_oauth_health_check_failed", error=str(e))
        return ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=None,
            last_check=timestamp,
            message="Connection failed",
        )


async def check_google_play() -> ProviderStatus:
    """Check Google Play Developer API reachability."""
    start = time.perf_counter()
    timestamp = datetime.now(UTC).isoformat()

    # If not configured, report as operational (not used)
    if not settings.PLAY_INTEGRITY_SERVICE_ACCOUNT:
        return ProviderStatus(
            status=StatusLevel.OPERATIONAL,
            latency_ms=0,
            last_check=timestamp,
            message="Not configured",
        )

    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT) as client:
            # Ping Google's API discovery endpoint (doesn't require auth)
            response = await client.get(
                "https://androidpublisher.googleapis.com/$discovery/rest?version=v3"
            )
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code == 200:
                status = (
                    StatusLevel.DEGRADED
                    if latency_ms > DEGRADED_LATENCY_THRESHOLD
                    else StatusLevel.OPERATIONAL
                )
                return ProviderStatus(
                    status=status,
                    latency_ms=latency_ms,
                    last_check=timestamp,
                    message="High latency" if status == StatusLevel.DEGRADED else None,
                )

            return ProviderStatus(
                status=StatusLevel.DEGRADED,
                latency_ms=latency_ms,
                last_check=timestamp,
                message=f"Unexpected status: {response.status_code}",
            )
    except httpx.TimeoutException:
        return ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=int(CHECK_TIMEOUT * 1000),
            last_check=timestamp,
            message="Timeout",
        )
    except Exception as e:
        logger.warning("google_play_health_check_failed", error=str(e))
        return ProviderStatus(
            status=StatusLevel.OUTAGE,
            latency_ms=None,
            last_check=timestamp,
            message="Connection failed",
        )


def calculate_overall_status(providers: dict[str, ProviderStatus]) -> StatusLevel:
    """Calculate overall service status from provider statuses."""
    statuses = [p.status for p in providers.values()]

    if StatusLevel.OUTAGE in statuses:
        return StatusLevel.OUTAGE
    if StatusLevel.DEGRADED in statuses:
        return StatusLevel.DEGRADED
    return StatusLevel.OPERATIONAL


@router.get("/v1/status", response_model=ServiceStatusResponse)
async def get_status() -> ServiceStatusResponse:
    """
    Get CIRISBilling service status.

    Public endpoint (no auth) for status page aggregation.
    Checks connectivity to all dependent providers.

    Rate limited via 10-second cache to prevent abuse.
    """
    global _status_cache

    # Check cache to prevent DoS via repeated requests
    cache_key = "status"
    now = datetime.now(UTC)

    if cache_key in _status_cache:
        cached_time, cached_response = _status_cache[cache_key]
        age_seconds = (now - cached_time).total_seconds()
        if age_seconds < _CACHE_TTL_SECONDS:
            logger.debug("status_cache_hit", age_seconds=age_seconds)
            return cached_response

    # Run all checks concurrently
    postgresql_task = asyncio.create_task(check_postgresql())
    google_oauth_task = asyncio.create_task(check_google_oauth())
    google_play_task = asyncio.create_task(check_google_play())

    postgresql_status = await postgresql_task
    google_oauth_status = await google_oauth_task
    google_play_status = await google_play_task

    providers = {
        "postgresql": postgresql_status,
        "google_oauth": google_oauth_status,
        "google_play": google_play_status,
    }

    overall_status = calculate_overall_status(providers)

    response = ServiceStatusResponse(
        service="cirisbilling",
        status=overall_status,
        timestamp=now.isoformat(),
        version=settings.api_version,
        providers=providers,
    )

    # Cache the response
    _status_cache[cache_key] = (now, response)

    return response
