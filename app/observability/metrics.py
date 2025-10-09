"""
Metrics Collection with Prometheus.

Exposes business and system metrics for monitoring.
"""

from enum import Enum
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram, Info

from app.config import settings


class MetricLabels(str, Enum):
    """Standard metric label names."""

    ENDPOINT = "endpoint"
    METHOD = "method"
    STATUS_CODE = "status_code"
    OPERATION = "operation"
    ACCOUNT_STATUS = "account_status"
    TRANSACTION_TYPE = "transaction_type"
    ERROR_TYPE = "error_type"


class BillingMetrics:
    """
    Centralized metrics for CIRIS Billing API.

    Minimum viable metrics covering:
    - HTTP requests (rate, duration, errors)
    - Credit checks (rate, success/failure)
    - Charges (rate, amount, success/failure)
    - Account operations (rate, balance changes)
    - Database operations (query duration, connection pool)
    """

    def __init__(self) -> None:
        """Initialize all Prometheus metrics."""

        # ====================================================================
        # Service Info
        # ====================================================================
        self.service_info = Info(
            "billing_service",
            "Service information",
        )
        self.service_info.info(
            {
                "version": settings.api_version,
                "service_name": settings.service_name,
            }
        )

        # ====================================================================
        # HTTP Metrics
        # ====================================================================
        self.http_requests_total = Counter(
            "billing_http_requests_total",
            "Total HTTP requests",
            [MetricLabels.ENDPOINT, MetricLabels.METHOD, MetricLabels.STATUS_CODE],
        )

        self.http_request_duration_seconds = Histogram(
            "billing_http_request_duration_seconds",
            "HTTP request duration in seconds",
            [MetricLabels.ENDPOINT, MetricLabels.METHOD],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        )

        self.http_requests_in_progress = Gauge(
            "billing_http_requests_in_progress",
            "Number of HTTP requests currently being processed",
            [MetricLabels.ENDPOINT, MetricLabels.METHOD],
        )

        # ====================================================================
        # Credit Check Metrics
        # ====================================================================
        self.credit_checks_total = Counter(
            "billing_credit_checks_total",
            "Total credit checks performed",
            ["has_credit", "reason"],
        )

        self.credit_check_duration_seconds = Histogram(
            "billing_credit_check_duration_seconds",
            "Credit check duration in seconds",
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
        )

        # ====================================================================
        # Charge Metrics
        # ====================================================================
        self.charges_total = Counter(
            "billing_charges_total",
            "Total charges created",
            ["success", MetricLabels.ERROR_TYPE],
        )

        self.charge_amount_minor = Histogram(
            "billing_charge_amount_minor",
            "Charge amounts in minor units (cents)",
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000),
        )

        self.charge_duration_seconds = Histogram(
            "billing_charge_duration_seconds",
            "Charge creation duration in seconds",
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
        )

        # ====================================================================
        # Credit Addition Metrics
        # ====================================================================
        self.credits_added_total = Counter(
            "billing_credits_added_total",
            "Total credits added to accounts",
            [MetricLabels.TRANSACTION_TYPE, "success"],
        )

        self.credit_amount_minor = Histogram(
            "billing_credit_amount_minor",
            "Credit amounts in minor units (cents)",
            buckets=(100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000),
        )

        # ====================================================================
        # Account Metrics
        # ====================================================================
        self.accounts_created_total = Counter(
            "billing_accounts_created_total",
            "Total accounts created",
        )

        self.account_balance_minor = Gauge(
            "billing_account_balance_minor",
            "Current account balance in minor units (gauge - sample)",
            [MetricLabels.ACCOUNT_STATUS],
        )

        # ====================================================================
        # Database Metrics
        # ====================================================================
        self.db_queries_total = Counter(
            "billing_db_queries_total",
            "Total database queries",
            [MetricLabels.OPERATION, "success"],
        )

        self.db_query_duration_seconds = Histogram(
            "billing_db_query_duration_seconds",
            "Database query duration in seconds",
            [MetricLabels.OPERATION],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
        )

        self.db_connections_active = Gauge(
            "billing_db_connections_active",
            "Number of active database connections",
        )

        self.db_write_verifications_total = Counter(
            "billing_db_write_verifications_total",
            "Total write verification checks",
            ["success"],
        )

        # ====================================================================
        # Error Metrics
        # ====================================================================
        self.errors_total = Counter(
            "billing_errors_total",
            "Total errors by type",
            [MetricLabels.ERROR_TYPE, MetricLabels.OPERATION],
        )

    # ========================================================================
    # Helper Methods
    # ========================================================================

    def record_http_request(
        self, endpoint: str, method: str, status_code: int, duration: float
    ) -> None:
        """Record HTTP request metrics."""
        self.http_requests_total.labels(
            endpoint=endpoint, method=method, status_code=status_code
        ).inc()
        self.http_request_duration_seconds.labels(endpoint=endpoint, method=method).observe(
            duration
        )

    def record_credit_check(self, has_credit: bool, reason: str | None, duration: float) -> None:
        """Record credit check metrics."""
        self.credit_checks_total.labels(
            has_credit=str(has_credit), reason=reason or "success"
        ).inc()
        self.credit_check_duration_seconds.observe(duration)

    def record_charge(
        self, success: bool, amount_minor: int, duration: float, error_type: str | None = None
    ) -> None:
        """Record charge creation metrics."""
        self.charges_total.labels(
            success=str(success), error_type=error_type or "none"
        ).inc()
        if success:
            self.charge_amount_minor.observe(amount_minor)
        self.charge_duration_seconds.observe(duration)

    def record_credit_addition(
        self,
        transaction_type: str,
        success: bool,
        amount_minor: int,
    ) -> None:
        """Record credit addition metrics."""
        self.credits_added_total.labels(
            transaction_type=transaction_type, success=str(success)
        ).inc()
        if success:
            self.credit_amount_minor.observe(amount_minor)

    def record_db_query(self, operation: str, success: bool, duration: float) -> None:
        """Record database query metrics."""
        self.db_queries_total.labels(operation=operation, success=str(success)).inc()
        self.db_query_duration_seconds.labels(operation=operation).observe(duration)

    def record_error(self, error_type: str, operation: str) -> None:
        """Record error occurrence."""
        self.errors_total.labels(error_type=error_type, operation=operation).inc()


# Global metrics instance
metrics = BillingMetrics()


# Context managers for automatic metric recording
class track_http_request:
    """
    Context manager for tracking HTTP requests.

    Usage:
        with track_http_request("/v1/billing/charges", "POST") as tracker:
            # ... process request
            tracker.set_status_code(201)
    """

    def __init__(self, endpoint: str, method: str) -> None:
        self.endpoint = endpoint
        self.method = method
        self.status_code = 200
        self.start_time: float = 0.0

    def set_status_code(self, status_code: int) -> None:
        """Set the response status code."""
        self.status_code = status_code

    def __enter__(self) -> "track_http_request":
        """Start tracking."""
        import time

        self.start_time = time.time()
        metrics.http_requests_in_progress.labels(
            endpoint=self.endpoint, method=self.method
        ).inc()
        return self

    def __exit__(self, exc_type: type, exc_val: Exception, exc_tb: object) -> None:
        """Record metrics."""
        import time

        duration = time.time() - self.start_time
        if exc_type is not None:
            self.status_code = 500  # Default to 500 on exception
        metrics.record_http_request(self.endpoint, self.method, self.status_code, duration)
        metrics.http_requests_in_progress.labels(
            endpoint=self.endpoint, method=self.method
        ).dec()


def get_metrics_handler() -> Callable[[], bytes]:
    """
    Get Prometheus metrics handler for FastAPI.

    Usage:
        from prometheus_client import generate_latest
        return get_metrics_handler()
    """
    from prometheus_client import REGISTRY, generate_latest

    def metrics_endpoint() -> bytes:
        return generate_latest(REGISTRY)

    return metrics_endpoint
