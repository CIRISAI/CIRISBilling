"""
Distributed Tracing with OpenTelemetry.

Provides end-to-end request tracing across services and databases.
"""

from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from app.config import settings


def setup_tracing() -> None:
    """
    Configure OpenTelemetry tracing with OTLP export.

    Sets up:
    - TracerProvider with service resource
    - OTLP exporter to collector
    - FastAPI auto-instrumentation
    - SQLAlchemy auto-instrumentation
    """
    if not settings.tracing_enabled:
        return

    # Create resource with service information
    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "service.version": settings.api_version,
            "deployment.environment": "production",
        }
    )

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add OTLP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint,
        insecure=settings.otlp_insecure,
    )
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Set as global tracer provider
    trace.set_tracer_provider(provider)


def instrument_fastapi(app: Any) -> None:
    """
    Instrument FastAPI application for automatic tracing.

    Must be called after app creation.
    """
    if not settings.tracing_enabled:
        return

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine: Any) -> None:
    """
    Instrument SQLAlchemy engine for automatic query tracing.

    Must be called for each database engine.
    """
    if not settings.tracing_enabled:
        return

    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)


def get_tracer(name: str) -> Tracer:
    """
    Get a tracer instance for manual span creation.

    Usage:
        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("operation_name") as span:
            span.set_attribute("key", "value")
            # ... do work
    """
    return trace.get_tracer(name)


def add_span_attributes(span: Span, **attributes: Any) -> None:
    """
    Add attributes to the current span.

    Usage:
        add_span_attributes(
            span,
            account_id=str(account_id),
            amount_minor=amount_minor,
            has_credit=has_credit
        )
    """
    for key, value in attributes.items():
        if value is not None:
            # Convert to string for non-primitive types
            if isinstance(value, (str, int, float, bool)):
                span.set_attribute(key, value)
            else:
                span.set_attribute(key, str(value))


def add_span_event(span: Span, name: str, **attributes: Any) -> None:
    """
    Add an event to the current span.

    Usage:
        add_span_event(
            span,
            "balance_updated",
            balance_before=1000,
            balance_after=900
        )
    """
    span.add_event(name, attributes=attributes)


def set_span_error(span: Span, error: Exception) -> None:
    """
    Mark span as error and record exception.

    Usage:
        try:
            # ... operation
        except Exception as e:
            set_span_error(span, e)
            raise
    """
    span.set_status(Status(StatusCode.ERROR, str(error)))
    span.record_exception(error)


# Context manager for manual span creation
class trace_operation:
    """
    Context manager for creating traced operations.

    Usage:
        with trace_operation("charge_creation", account_id=account_id) as span:
            # ... perform operation
            span.set_attribute("amount", 100)
    """

    def __init__(self, operation_name: str, **attributes: Any) -> None:
        self.operation_name = operation_name
        self.attributes = attributes
        self.span: Span | None = None
        self.tracer = get_tracer("app.operations")

    def __enter__(self) -> Span:
        """Start span."""
        self.span = self.tracer.start_span(self.operation_name)
        if self.span:
            add_span_attributes(self.span, **self.attributes)
        # Make it the current span
        self.token = trace.set_span_in_context(self.span).__enter__()
        return self.span

    def __exit__(self, exc_type: type, exc_val: Exception, exc_tb: object) -> None:
        """End span and record any errors."""
        if self.span:
            if exc_val:
                set_span_error(self.span, exc_val)
            self.span.end()
