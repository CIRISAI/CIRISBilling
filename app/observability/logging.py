"""
Structured Logging with Structlog.

Provides JSON-formatted logs with correlation IDs and context.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.config import settings


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
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )

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
    return structlog.get_logger(name)  # type: ignore[return-value]


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
