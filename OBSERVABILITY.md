# CIRIS Billing API - Observability Guide

Complete guide to logs, metrics, and traces using OpenTelemetry.

## Overview

The CIRIS Billing API implements the **three pillars of observability**:

1. **Logs**: Structured JSON logs with correlation IDs (structlog)
2. **Metrics**: Business and system metrics (Prometheus)
3. **Traces**: Distributed request tracing (OpenTelemetry → Jaeger)

All telemetry is exported via **OTLP (OpenTelemetry Protocol)** to a collector, making it vendor-neutral and future-proof.

```
┌──────────────────┐
│  Billing API     │
│  (FastAPI)       │
└────────┬─────────┘
         │
         │ OTLP (gRPC/HTTP)
         │
         ▼
┌──────────────────┐
│  OTel Collector  │  ← Central telemetry hub
└────────┬─────────┘
         │
         ├─────► Jaeger (Traces)
         ├─────► Prometheus (Metrics)
         └─────► Loki (Logs - optional)
                   │
                   ▼
              ┌─────────┐
              │ Grafana │ ← Unified UI
              └─────────┘
```

## Quick Start

### Start Observability Stack

```bash
# Start main services
docker-compose up -d

# Start observability stack
docker-compose -f docker-compose.observability.yml up -d

# View all services
docker-compose ps
```

### Access UIs

| Service | URL | Credentials |
|---------|-----|-------------|
| **API Metrics** | http://localhost:8080/metrics | None |
| **Grafana** | http://localhost:3000 | admin/admin |
| **Prometheus** | http://localhost:9091 | None |
| **Jaeger** | http://localhost:16686 | None |

## 1. Structured Logging

### Configuration

Logs are configured in `app/config.py`:

```python
# Environment variables
LOG_LEVEL=INFO          # DEBUG, INFO, WARN, ERROR
LOG_FORMAT=json         # json or console
```

### Log Format

All logs are JSON-formatted with standard fields:

```json
{
  "event": "request_completed",
  "level": "info",
  "timestamp": "2025-01-08T12:34:56.789Z",
  "logger": "app.main",
  "service": "ciris-billing-api",
  "version": "0.1.0",
  "request_id": "req-abc123",
  "method": "POST",
  "path": "/v1/billing/charges",
  "status_code": 201,
  "duration_seconds": 0.042
}
```

### Using Logger in Code

```python
from app.observability import get_logger

logger = get_logger(__name__)

# Simple log
logger.info("operation_started")

# Log with context
logger.info(
    "charge_created",
    account_id=str(account_id),
    amount_minor=100,
    balance_after=900
)

# Error log
logger.error(
    "charge_failed",
    account_id=str(account_id),
    error=str(exc),
    exc_info=True  # Include traceback
)
```

### Adding Request Context

```python
from app.observability.logging import log_context

with log_context(request_id="req-123", user_id="user-456"):
    logger.info("processing_request")
    # All logs within this block include request_id and user_id
```

### Viewing Logs

```bash
# Follow logs from all API instances
docker-compose logs -f billing-api-1 billing-api-2 billing-api-3

# Filter JSON logs with jq
docker-compose logs billing-api-1 | jq 'select(.level=="error")'

# Find logs for specific request
docker-compose logs billing-api-1 | jq 'select(.request_id=="req-123")'
```

## 2. Metrics

### Available Metrics

The API exposes **60+ metrics** across these categories:

#### HTTP Metrics
- `billing_http_requests_total` - Total requests by endpoint, method, status
- `billing_http_request_duration_seconds` - Request latency histogram
- `billing_http_requests_in_progress` - Current active requests

#### Credit Check Metrics
- `billing_credit_checks_total` - Total checks by result
- `billing_credit_check_duration_seconds` - Check latency

#### Charge Metrics
- `billing_charges_total` - Total charges by success/failure
- `billing_charge_amount_minor` - Charge amount distribution
- `billing_charge_duration_seconds` - Charge operation latency

#### Credit Addition Metrics
- `billing_credits_added_total` - Total credits by transaction type
- `billing_credit_amount_minor` - Credit amount distribution

#### Database Metrics
- `billing_db_queries_total` - Total queries by operation
- `billing_db_query_duration_seconds` - Query latency
- `billing_db_connections_active` - Active database connections
- `billing_db_write_verifications_total` - Write verification results

#### Error Metrics
- `billing_errors_total` - Total errors by type and operation

### Querying Metrics

**View raw metrics**:
```bash
curl http://localhost:8080/metrics
```

**Prometheus queries** (see OBSERVABILITY_QUERIES.md for full list):

```promql
# Request rate
rate(billing_http_requests_total[1m])

# Error rate
sum(rate(billing_http_requests_total{status_code=~"5.."}[1m]))
  /
sum(rate(billing_http_requests_total[1m]))

# p95 response time
histogram_quantile(0.95,
  sum(rate(billing_http_request_duration_seconds_bucket[5m])) by (le)
)
```

### Recording Custom Metrics

```python
from app.observability import metrics

# Record credit check
metrics.record_credit_check(
    has_credit=True,
    reason=None,
    duration=0.005
)

# Record charge
metrics.record_charge(
    success=True,
    amount_minor=100,
    duration=0.042
)

# Record error
metrics.record_error(
    error_type="InsufficientCreditsError",
    operation="create_charge"
)
```

## 3. Distributed Tracing

### How It Works

Every request through the API creates a **trace** with multiple **spans**:

```
Trace: POST /v1/billing/charges
├─ Span: HTTP POST /v1/billing/charges (100ms)
│  ├─ Span: BillingService.create_charge (80ms)
│  │  ├─ Span: SELECT account FOR UPDATE (10ms)
│  │  ├─ Span: INSERT INTO charges (5ms)
│  │  ├─ Span: UPDATE accounts (3ms)
│  │  └─ Span: SELECT charge verification (2ms)
│  └─ Span: Response serialization (5ms)
```

### Viewing Traces in Jaeger

1. Open http://localhost:16686
2. Select service: `ciris-billing-api`
3. Click "Find Traces"

**Search options**:
- By operation: `POST /v1/billing/charges`
- By duration: Min duration > 1s
- By tag: `error=true`, `account_id=...`
- By trace ID: Paste trace ID from logs

### Trace Context Propagation

Traces automatically propagate across:
- ✅ HTTP requests (via headers)
- ✅ Database queries
- ✅ Internal function calls

**Trace ID in logs**:
```json
{
  "event": "charge_created",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  ...
}
```

### Adding Custom Spans

```python
from app.observability.tracing import trace_operation, add_span_attributes

# Automatic span
with trace_operation("validation", account_id=account_id) as span:
    # ... perform validation
    span.set_attribute("validation_result", "passed")

# Manual span
from opentelemetry import trace

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("custom_operation") as span:
    span.set_attribute("key", "value")
    span.add_event("operation_completed")
```

## 4. OTLP Export Configuration

### Environment Variables

```bash
# Tracing
TRACING_ENABLED=true
OTLP_ENDPOINT=http://otel-collector:4317
OTLP_INSECURE=true
SERVICE_NAME=ciris-billing-api
TRACE_SAMPLE_RATE=1.0  # 1.0 = 100% sampling

# Metrics
METRICS_ENABLED=true
METRICS_PORT=9090
```

### Collector Configuration

The OpenTelemetry Collector receives all telemetry and exports to backends:

```yaml
# docker/otel/otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

exporters:
  otlp/jaeger:
    endpoint: jaeger:4317
  prometheus:
    endpoint: "0.0.0.0:8889"

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlp/jaeger]
    metrics:
      receivers: [otlp]
      exporters: [prometheus]
```

## 5. Grafana Dashboards

### Default Datasources

Grafana comes pre-configured with:
- **Prometheus**: Metrics queries
- **Jaeger**: Trace exploration

### Creating Dashboards

1. Login to Grafana (http://localhost:3000)
2. Go to Dashboards → New Dashboard
3. Add panels with queries from OBSERVABILITY_QUERIES.md

### Example Panel: Request Rate

```json
{
  "title": "Request Rate",
  "targets": [{
    "expr": "sum(rate(billing_http_requests_total[1m]))",
    "legendFormat": "Requests/sec"
  }],
  "yAxis": {
    "label": "Requests/sec"
  }
}
```

### Recommended Dashboards

1. **API Overview**
   - Request rate, error rate, latency
   - Active requests gauge
   - Top endpoints table

2. **Business Metrics**
   - Credit checks (success rate, denial reasons)
   - Charges (volume, amounts, success rate)
   - Account operations

3. **Database Performance**
   - Query duration by operation
   - Active connections
   - Write verification success rate

4. **Error Monitoring**
   - Error rate over time
   - Errors by type and operation
   - Failed transactions

## 6. Production Best Practices

### Sampling

For high-traffic production systems, reduce trace sampling:

```bash
TRACE_SAMPLE_RATE=0.1  # 10% sampling
```

**Strategies**:
- Sample 100% of errors
- Sample 100% of slow requests (> 1s)
- Sample 10% of normal requests

### Log Retention

Configure log rotation:

```yaml
# docker-compose.yml
services:
  billing-api-1:
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "10"
```

### Metric Retention

Prometheus retention (in docker/prometheus/prometheus.yml):

```yaml
global:
  scrape_interval: 15s

# Command args in docker-compose
command:
  - '--storage.tsdb.retention.time=30d'
```

### Alert Rules

Create alerts in Prometheus (see OBSERVABILITY_QUERIES.md):

```yaml
groups:
  - name: billing_api
    interval: 30s
    rules:
      - alert: HighErrorRate
        expr: rate(billing_http_requests_total{status_code=~"5.."}[1m]) > 0.01
        for: 5m
        annotations:
          summary: "High error rate detected"
```

### Exporting to External Systems

The OTLP collector can export to:
- **Datadog**: Add Datadog exporter
- **New Relic**: Add OTLP endpoint
- **Honeycomb**: Configure Honeycomb exporter
- **Elastic APM**: Add Elastic exporter

Example (Datadog):
```yaml
exporters:
  datadog:
    api:
      key: ${DATADOG_API_KEY}

service:
  pipelines:
    traces:
      exporters: [datadog]
```

## 7. Debugging Workflows

### Debugging a Slow Request

1. **Find in Jaeger**
   - Search by time range
   - Filter by min duration: 1s
   - Click trace to expand

2. **Identify Bottleneck**
   - Look for longest span
   - Check database query spans
   - Review span attributes

3. **Check Logs**
   - Copy request_id from trace
   - Query logs: `docker-compose logs | grep "request_id=..."`

4. **Review Metrics**
   - Check endpoint-specific latency in Grafana
   - Compare to historical data

### Investigating Errors

1. **Check Error Rate** (Grafana)
   - Is it spiking?
   - Which endpoints?

2. **Query Error Logs**
   ```bash
   docker-compose logs | jq 'select(.level=="error")' | tail -50
   ```

3. **Find Failed Traces** (Jaeger)
   - Filter by tag: `error=true`
   - Review exception details

4. **Check Metrics**
   ```promql
   sum by (error_type, operation) (rate(billing_errors_total[5m]))
   ```

### Performance Analysis

1. **Identify Slow Endpoints**
   ```promql
   topk(5,
     histogram_quantile(0.95,
       sum by (le, endpoint) (rate(billing_http_request_duration_seconds_bucket[5m]))
     )
   )
   ```

2. **Analyze Database Queries**
   ```promql
   histogram_quantile(0.95,
     sum by (le, operation) (rate(billing_db_query_duration_seconds_bucket[5m]))
   )
   ```

3. **Check Traces for Slow Operations**
   - Jaeger: Min duration > 500ms
   - Expand spans to find bottlenecks

## 8. Troubleshooting

### No Metrics Appearing

1. Check metrics endpoint:
   ```bash
   curl http://localhost:8080/metrics
   ```

2. Check Prometheus targets:
   - Open http://localhost:9091/targets
   - Verify billing-api targets are UP

3. Check collector logs:
   ```bash
   docker-compose -f docker-compose.observability.yml logs otel-collector
   ```

### No Traces in Jaeger

1. Verify tracing is enabled:
   ```bash
   curl http://localhost:8080/ | jq '.tracing_enabled'
   ```

2. Check OTLP endpoint connectivity:
   ```bash
   docker-compose exec billing-api-1 ping -c 3 otel-collector
   ```

3. Check collector logs for trace ingestion

### Logs Not Structured

1. Verify LOG_FORMAT=json in environment
2. Restart API containers
3. Check startup logs for configuration

## 9. Cost Optimization

### Reduce Telemetry Volume

**Sampling**:
- Traces: 10% sampling for normal traffic
- Metrics: Increase scrape interval to 30s
- Logs: Set LOG_LEVEL=WARN in production

**Retention**:
- Metrics: 15d instead of 30d
- Traces: 3d instead of 7d
- Logs: 7d instead of 30d

**Filtering**:
```yaml
# Only export errors to external system
processors:
  filter:
    traces:
      span:
        - 'attributes["error"] == true'
```

## 10. Summary

| Signal | Tech | Endpoint | Use Case |
|--------|------|----------|----------|
| **Logs** | structlog | Docker logs | Debugging, audit trail |
| **Metrics** | Prometheus | http://localhost:9091 | Monitoring, alerting |
| **Traces** | OpenTelemetry/Jaeger | http://localhost:16686 | Performance analysis |
| **Dashboards** | Grafana | http://localhost:3000 | Unified visualization |

### Key Files

- `app/observability/logging.py` - Structured logging setup
- `app/observability/metrics.py` - Metrics definitions
- `app/observability/tracing.py` - Tracing instrumentation
- `docker/otel/otel-collector-config.yaml` - Collector configuration
- `OBSERVABILITY_QUERIES.md` - Prometheus/Jaeger query examples

---

**For more queries and dashboard examples**, see [OBSERVABILITY_QUERIES.md](./OBSERVABILITY_QUERIES.md)
