"""
Main Application - FastAPI application setup.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from prometheus_client import generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_auth_routes import router as admin_auth_router
from app.api.admin_routes import router as admin_router
from app.api.routes import router
from app.api.status_routes import router as status_router
from app.api.tool_routes import router as tool_router
from app.config import settings
from app.db.migration_runner import run_migrations
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

    # Run database migrations in a thread to avoid event loop conflicts
    # (alembic's env.py uses asyncio.run() which can't run in an existing loop)
    import asyncio

    try:
        logger.info("running_database_migrations")
        await asyncio.to_thread(run_migrations)
        logger.info("database_migrations_complete")
    except Exception as e:
        logger.error("database_migrations_failed", error=str(e))
        raise

    yield

    # Shutdown
    logger.info("application_shutting_down")
    await close_engines()
    logger.info("database_engines_closed")


# Disable docs in production
_is_production = settings.environment.lower() == "production"

app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description,
    lifespan=lifespan,
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)


# Add validation error logging handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Log detailed validation errors for debugging."""
    errors = exc.errors()

    # Sanitize errors for JSON serialization (ctx may contain non-serializable objects)
    sanitized_errors = []
    for error in errors:
        sanitized = {
            "type": error.get("type"),
            "loc": error.get("loc"),
            "msg": error.get("msg"),
            "input": error.get("input"),
        }
        # Convert ctx values to strings if present
        if "ctx" in error:
            sanitized["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        sanitized_errors.append(sanitized)

    logger.warning(
        "validation_error",
        path=request.url.path,
        method=request.method,
        errors=sanitized_errors,
        body_preview=str(exc.body)[:500] if exc.body else None,
    )
    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )


# Setup tracing
setup_tracing()
instrument_fastapi(app)


# Proxy headers middleware - trust X-Forwarded-* headers from nginx
class ProxyHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to handle X-Forwarded-* headers from reverse proxy."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
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
async def logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log all HTTP requests with timing."""
    import time

    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", "unknown")

    # Only log request start at DEBUG level to reduce noise
    logger.debug(
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

        # Only log at INFO level for errors or slow requests (>1s)
        if response.status_code >= 400 or duration > 1.0:
            logger.info(
                "request_completed",
                method=method,
                path=endpoint,
                status_code=response.status_code,
                duration_seconds=duration,
                request_id=request_id,
            )
        else:
            logger.debug(
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
app.include_router(tool_router)  # Tool credits API (web search, etc.)
app.include_router(status_router)  # Status/health checks (public)
app.include_router(admin_auth_router)  # Admin OAuth routes
app.include_router(admin_router)  # Admin API routes

# Serve admin UI with authentication check
_static_dir = Path(__file__).parent.parent / "static" / "admin"


@app.get("/admin-ui/login.html")
async def admin_login_page() -> Response:
    """Serve login page (public)."""
    login_file = _static_dir / "login.html"
    if login_file.exists():
        return Response(content=login_file.read_text(), media_type="text/html")
    raise HTTPException(status_code=404, detail="Login page not found")


@app.get("/admin-ui/{path:path}")
async def admin_ui_protected(path: str, request: Request) -> Response:
    """Serve admin UI files - HTML requires authentication, static assets are public."""
    from structlog import get_logger

    logger = get_logger(__name__)

    # Serve the requested file
    if not path or path == "":
        path = "index.html"

    file_path = _static_dir / path
    if not file_path.exists() or not file_path.is_file():
        # Try with .html extension
        file_path = _static_dir / f"{path}.html"

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Security: ensure path doesn't escape static dir
    try:
        file_path.resolve().relative_to(_static_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine content type
    suffix = file_path.suffix.lower()
    content_types = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }
    content_type = content_types.get(suffix, "application/octet-stream")

    # Only require auth for HTML pages (not JS, CSS, images, etc.)
    # Static assets need to load for the login page to work
    if suffix == ".html" and path != "login.html":
        token = request.cookies.get("admin_token")
        all_cookies = dict(request.cookies)
        logger.info(
            "admin_ui_auth_check",
            path=path,
            has_token=bool(token),
            cookie_keys=list(all_cookies.keys()),
            token_preview=token[:20] + "..." if token and len(token) > 20 else token,
        )
        if not token:
            return RedirectResponse(url="/admin-ui/login.html", status_code=302)

    return Response(content=file_path.read_bytes(), media_type=content_type)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": settings.api_title,
        "version": settings.api_version,
        "status": "running",
    }


def _is_internal_ip(ip: str) -> bool:
    """Check if IP is localhost or private network."""
    return (
        ip in ("127.0.0.1", "::1", "localhost")
        or ip.startswith("10.")
        or ip.startswith("172.")
        or ip.startswith("192.168.")
    )


@app.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text format.
    In production, only accessible from localhost/internal networks.
    """
    if _is_production:
        # Check real client IP (X-Forwarded-For from reverse proxy)
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        real_ip = request.headers.get("X-Real-IP", "")
        client_ip = request.client.host if request.client else ""

        # Use forwarded IP if present (first IP in chain is original client)
        if forwarded_for:
            actual_ip = forwarded_for.split(",")[0].strip()
        elif real_ip:
            actual_ip = real_ip
        else:
            actual_ip = client_ip

        if not _is_internal_ip(actual_ip):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
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
