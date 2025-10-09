# Observability Quick Start Guide

Get logs, metrics, and traces running in 5 minutes.

## Prerequisites

- Docker and Docker Compose installed
- CIRIS Billing API running (`make start`)

## Step 1: Start Observability Stack (30 seconds)

```bash
# Start OpenTelemetry Collector, Jaeger, Prometheus, Grafana
make obs-start

# Or manually:
docker-compose -f docker-compose.observability.yml up -d
```

**Output**:
```
Observability stack started:
  Grafana:    http://localhost:3000 (admin/admin)
  Prometheus: http://localhost:9091
  Jaeger:     http://localhost:16686
```

## Step 2: Generate Some Traffic (1 minute)

```bash
# Create an account
curl -X POST http://localhost:8080/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "demo@example.com",
    "initial_balance_minor": 10000,
    "currency": "USD",
    "plan_name": "demo"
  }'

# Check credit
curl -X POST http://localhost:8080/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "demo@example.com",
    "context": {}
  }'

# Create a charge
curl -X POST http://localhost:8080/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "demo@example.com",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Demo charge",
    "metadata": {}
  }'

# Repeat a few times
for i in {1..10}; do
  curl -s -X POST http://localhost:8080/v1/billing/credits/check \
    -H "Content-Type: application/json" \
    -d '{"oauth_provider":"oauth:google","external_id":"demo@example.com","context":{}}' \
    > /dev/null
  echo "Request $i sent"
done
```

## Step 3: View Logs (Immediate)

```bash
# View structured JSON logs
docker-compose logs billing-api-1 | tail -20

# Pretty-print with jq
docker-compose logs billing-api-1 | jq 'select(.event == "request_completed")'

# Filter by level
docker-compose logs billing-api-1 | jq 'select(.level == "error")'

# Find logs for specific request
docker-compose logs billing-api-1 | jq 'select(.request_id == "...")'
```

**Example log entry**:
```json
{
  "event": "request_completed",
  "level": "info",
  "timestamp": "2025-01-08T12:34:56.789Z",
  "logger": "app.main",
  "service": "ciris-billing-api",
  "version": "0.1.0",
  "method": "POST",
  "path": "/v1/billing/credits/check",
  "status_code": 200,
  "duration_seconds": 0.0042
}
```

## Step 4: View Metrics (30 seconds)

### Raw Metrics
```bash
curl http://localhost:8080/metrics | grep billing_
```

### Prometheus UI
1. Open http://localhost:9091
2. Try these queries in the "Graph" tab:

**Request rate**:
```promql
rate(billing_http_requests_total[1m])
```

**Credit check rate**:
```promql
rate(billing_credit_checks_total[1m])
```

**Response time (p95)**:
```promql
histogram_quantile(0.95, sum(rate(billing_http_request_duration_seconds_bucket[5m])) by (le))
```

## Step 5: View Traces (1 minute)

1. Open http://localhost:16686 (Jaeger UI)
2. Select Service: `ciris-billing-api`
3. Click **"Find Traces"**
4. Click on any trace to expand

**What you'll see**:
- HTTP request span
- Service method spans
- Database query spans
- Timing for each operation

**Example trace structure**:
```
POST /v1/billing/charges (45ms)
├─ BillingService.create_charge (38ms)
│  ├─ SELECT account FOR UPDATE (8ms)
│  ├─ INSERT INTO charges (12ms)
│  ├─ UPDATE accounts (10ms)
│  └─ SELECT charge verification (3ms)
└─ Response serialization (2ms)
```

## Step 6: Create Grafana Dashboard (2 minutes)

1. Open http://localhost:3000
2. Login: **admin** / **admin**
3. Click **+** → **Create Dashboard**
4. Click **Add visualization**
5. Select **Prometheus** datasource
6. Enter query: `rate(billing_http_requests_total[1m])`
7. Click **Apply**

**Pre-built dashboard panels**:
- Request rate graph
- Error rate gauge
- p95 latency graph
- Active requests gauge
- Top endpoints table

See [OBSERVABILITY_QUERIES.md](./OBSERVABILITY_QUERIES.md) for more queries.

## Quick Commands

```bash
# Start observability
make obs-start

# View metrics in browser
make metrics        # Opens Prometheus

# View traces in browser
make traces         # Opens Jaeger

# View dashboards
make dashboards     # Opens Grafana

# View logs
make logs

# Stop observability
make obs-stop
```

## Troubleshooting

### No metrics showing?
```bash
# Check metrics endpoint
curl http://localhost:8080/metrics

# Check Prometheus targets
open http://localhost:9091/targets
# All billing-api-* should show "UP"
```

### No traces in Jaeger?
```bash
# Check collector is receiving data
docker-compose -f docker-compose.observability.yml logs otel-collector | grep -i trace

# Check network connectivity
docker-compose exec billing-api-1 ping otel-collector
```

### Logs not structured?
```bash
# Verify configuration
docker-compose exec billing-api-1 env | grep LOG_
# Should show: LOG_FORMAT=json
```

## What's Next?

- **[OBSERVABILITY.md](./OBSERVABILITY.md)** - Complete observability guide
- **[OBSERVABILITY_QUERIES.md](./OBSERVABILITY_QUERIES.md)** - Query examples
- **Create alerts** - Add Prometheus alert rules
- **Custom dashboards** - Build business metric dashboards
- **Export to external** - Send to Datadog, New Relic, etc.

## Summary

| Feature | Access | Notes |
|---------|--------|-------|
| **Logs** | `docker-compose logs` | Structured JSON with correlation IDs |
| **Metrics** | http://localhost:9091 | 60+ Prometheus metrics |
| **Traces** | http://localhost:16686 | Distributed tracing with Jaeger |
| **Dashboards** | http://localhost:3000 | Grafana visualizations |
| **Raw Metrics** | http://localhost:8080/metrics | Prometheus text format |

🎉 **You now have full observability!**
