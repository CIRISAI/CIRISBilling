"""
Main Application - FastAPI application setup.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_auth_routes import router as admin_auth_router
from app.api.admin_routes import router as admin_router
from app.api.routes import router
from app.config import settings
from app.db.session import close_engines
from app.observability import get_logger, metrics, setup_logging, setup_tracing
from app.observability.tracing import instrument_fastapi

# Setup logging before anything else
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info(
        "application_starting",
        service=settings.api_title,
        version=settings.api_version,
        tracing_enabled=settings.tracing_enabled,
        metrics_enabled=settings.metrics_enabled,
    )

    yield

    # Shutdown
    logger.info("application_shutting_down")
    await close_engines()
    logger.info("database_engines_closed")


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
)

# Setup tracing
setup_tracing()
instrument_fastapi(app)

# Proxy headers middleware - trust X-Forwarded-* headers from nginx
class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to handle X-Forwarded-* headers from reverse proxy."""

    async def dispatch(self, request: Request, call_next):
        # Fix scheme based on X-Forwarded-Proto header
        forwarded_proto = request.headers.get("X-Forwarded-Proto")
        if forwarded_proto:
            # Update request scope to reflect actual protocol
            request.scope["scheme"] = forwarded_proto

        response = await call_next(request)
        return response

app.add_middleware(ProxyHeadersMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log all HTTP requests with timing."""
    import time

    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", "unknown")

    logger.info(
        "request_started",
        method=request.method,
        path=request.url.path,
        request_id=request_id,
    )

    # Track in-progress requests
    endpoint = request.url.path
    method = request.method
    metrics.http_requests_in_progress.labels(endpoint=endpoint, method=method).inc()

    try:
        response = await call_next(request)
        duration = time.time() - start_time

        # Record metrics
        metrics.record_http_request(endpoint, method, response.status_code, duration)

        logger.info(
            "request_completed",
            method=method,
            path=endpoint,
            status_code=response.status_code,
            duration_seconds=duration,
            request_id=request_id,
        )

        return response
    except Exception as e:
        duration = time.time() - start_time
        metrics.record_http_request(endpoint, method, 500, duration)
        metrics.record_error(type(e).__name__, "http_request")

        logger.error(
            "request_failed",
            method=method,
            path=endpoint,
            error=str(e),
            duration_seconds=duration,
            request_id=request_id,
            exc_info=True,
        )
        raise
    finally:
        metrics.http_requests_in_progress.labels(endpoint=endpoint, method=method).dec()


# Register routes
app.include_router(router)  # Billing API routes (for agents)
app.include_router(admin_auth_router)  # Admin OAuth routes
app.include_router(admin_router)  # Admin API routes


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": settings.api_title,
        "version": settings.api_version,
        "status": "running",
    }


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text format.
    """
    return PlainTextResponse(generate_latest())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level=settings.log_level.lower(),
    )
