"""
Structured Logging with Structlog.

Provides JSON-formatted logs with correlation IDs and context.
Ships logs to CIRISLens when CIRISLENS_TOKEN is configured.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.config import settings

# LogShipper instance (initialized when CIRISLENS_TOKEN is set)
_log_shipper = None


def add_app_context(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add application-level context to all log entries."""
    event_dict["service"] = settings.service_name
    event_dict["version"] = settings.api_version
    return event_dict


def setup_logging() -> None:
    """
    Configure structured logging with structlog.

    Logs are formatted as JSON for machine parsing with the following structure:
    {
        "event": "message",
        "level": "info",
        "timestamp": "2025-01-08T12:00:00.123456Z",
        "logger": "app.services.billing",
        "service": "ciris-billing-api",
        "version": "0.1.0",
        "request_id": "req-123",
        ...additional context
    }

    When CIRISLENS_TOKEN is set, logs are also shipped to CIRISLens.
    """
    global _log_shipper

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

    # Setup CIRISLens log shipping if token is configured
    if settings.cirislens_token:
        try:
            from app.logshipper import LogShipper, LogShipperHandler

            _log_shipper = LogShipper(
                service_name=settings.service_name,
                token=settings.cirislens_token,
                endpoint=settings.cirislens_endpoint,
                batch_size=100,
                flush_interval=5.0,
            )

            # Add handler to root logger
            handler = LogShipperHandler(_log_shipper, min_level=logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logging.getLogger().addHandler(handler)

            # Log that CIRISLens is enabled (this will also be shipped)
            logging.info(
                "CIRISLens log shipping enabled",
                extra={"event": "cirislens_enabled", "endpoint": settings.cirislens_endpoint},
            )
        except Exception as e:
            logging.warning(f"Failed to initialize CIRISLens LogShipper: {e}")

    # Processors for structlog
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        add_app_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    # Add exception info
    if settings.log_level.upper() == "DEBUG":
        processors.append(structlog.processors.ExceptionRenderer())
    else:
        processors.append(structlog.processors.format_exc_info)

    # Choose renderer based on format
    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Usage:
        logger = get_logger(__name__)
        logger.info("credit_check_performed", account_id=account_id, has_credit=True)
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def get_log_shipper_stats() -> dict | None:
    """Get CIRISLens log shipper stats (if enabled)."""
    if _log_shipper:
        return _log_shipper.get_stats()
    return None


# Context manager for adding request context
class log_context:
    """
    Context manager for adding structured logging context.

    Usage:
        with log_context(request_id="req-123", user_id="user-456"):
            logger.info("processing_request")
            # All logs within this context will include request_id and user_id
    """

    def __init__(self, **kwargs: Any) -> None:
        self.context = kwargs
        self.token = None

    def __enter__(self) -> None:
        """Enter context - bind context variables."""
        for key, value in self.context.items():
            structlog.contextvars.bind_contextvars(**{key: value})

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context - clear context variables."""
        for key in self.context.keys():
            structlog.contextvars.unbind_contextvars(key)
