# Observability Queries and Dashboards

## Prometheus Queries

### HTTP Metrics

**Request Rate (per second)**
```promql
rate(billing_http_requests_total[1m])
```

**Request Rate by Endpoint**
```promql
sum by (endpoint, method) (rate(billing_http_requests_total[1m]))
```

**Error Rate**
```promql
sum(rate(billing_http_requests_total{status_code=~"5.."}[1m]))
  /
sum(rate(billing_http_requests_total[1m]))
```

**95th Percentile Response Time**
```promql
histogram_quantile(0.95,
  sum by (le, endpoint) (rate(billing_http_request_duration_seconds_bucket[5m]))
)
```

**Requests in Progress**
```promql
sum(billing_http_requests_in_progress)
```

### Credit Check Metrics

**Credit Check Rate**
```promql
rate(billing_credit_checks_total[1m])
```

**Credit Check Success Rate**
```promql
sum(rate(billing_credit_checks_total{has_credit="True"}[1m]))
  /
sum(rate(billing_credit_checks_total[1m]))
```

**Credit Check Duration (p50, p95, p99)**
```promql
# p50
histogram_quantile(0.50, sum(rate(billing_credit_check_duration_seconds_bucket[5m])) by (le))

# p95
histogram_quantile(0.95, sum(rate(billing_credit_check_duration_seconds_bucket[5m])) by (le))

# p99
histogram_quantile(0.99, sum(rate(billing_credit_check_duration_seconds_bucket[5m])) by (le))
```

**Credit Denials by Reason**
```promql
sum by (reason) (rate(billing_credit_checks_total{has_credit="False"}[5m]))
```

### Charge Metrics

**Charge Creation Rate**
```promql
rate(billing_charges_total[1m])
```

**Charge Success Rate**
```promql
sum(rate(billing_charges_total{success="True"}[1m]))
  /
sum(rate(billing_charges_total[1m]))
```

**Charge Failure Rate by Error Type**
```promql
sum by (error_type) (rate(billing_charges_total{success="False"}[1m]))
```

**Average Charge Amount**
```promql
rate(billing_charge_amount_minor_sum[5m])
  /
rate(billing_charge_amount_minor_count[5m])
```

**Charge Amount Distribution (p50, p95, p99)**
```promql
# p50
histogram_quantile(0.50, sum(rate(billing_charge_amount_minor_bucket[5m])) by (le))

# p95
histogram_quantile(0.95, sum(rate(billing_charge_amount_minor_bucket[5m])) by (le))

# p99
histogram_quantile(0.99, sum(rate(billing_charge_amount_minor_bucket[5m])) by (le))
```

**Charge Duration**
```promql
histogram_quantile(0.95, sum(rate(billing_charge_duration_seconds_bucket[5m])) by (le))
```

### Credit Addition Metrics

**Credit Addition Rate by Type**
```promql
sum by (transaction_type) (rate(billing_credits_added_total[1m]))
```

**Credits Added (Total Value per Minute)**
```promql
rate(billing_credit_amount_minor_sum[1m]) * 60
```

### Database Metrics

**Database Query Rate**
```promql
sum by (operation) (rate(billing_db_queries_total[1m]))
```

**Database Query Success Rate**
```promql
sum(rate(billing_db_queries_total{success="True"}[1m]))
  /
sum(rate(billing_db_queries_total[1m]))
```

**Database Query Duration by Operation**
```promql
histogram_quantile(0.95,
  sum by (le, operation) (rate(billing_db_query_duration_seconds_bucket[5m]))
)
```

**Active Database Connections**
```promql
billing_db_connections_active
```

**Write Verification Success Rate**
```promql
sum(rate(billing_db_write_verifications_total{success="True"}[1m]))
  /
sum(rate(billing_db_write_verifications_total[1m]))
```

### Error Metrics

**Total Error Rate**
```promql
sum(rate(billing_errors_total[1m]))
```

**Errors by Type and Operation**
```promql
sum by (error_type, operation) (rate(billing_errors_total[5m]))
```

### Account Metrics

**Accounts Created per Minute**
```promql
rate(billing_accounts_created_total[1m]) * 60
```

**Sample Account Balance Distribution**
```promql
billing_account_balance_minor
```

## Grafana Dashboard Panels

### Panel 1: Request Rate
- **Type**: Graph
- **Query**: `sum(rate(billing_http_requests_total[1m]))`
- **Y-axis**: Requests/sec

### Panel 2: Error Rate
- **Type**: Graph
- **Query**: Error rate formula (see above)
- **Y-axis**: Percentage
- **Alert**: When > 1%

### Panel 3: Response Time (p95)
- **Type**: Graph
- **Query**: p95 response time (see above)
- **Y-axis**: Seconds
- **Alert**: When > 1 second

### Panel 4: Credit Check Success Rate
- **Type**: Stat
- **Query**: Credit check success rate (see above)
- **Unit**: Percent (0-100)

### Panel 5: Charge Volume
- **Type**: Graph
- **Query**: `rate(billing_charges_total[1m])`
- **Y-axis**: Charges/sec

### Panel 6: Database Query Duration
- **Type**: Heatmap
- **Query**: Database query duration by operation
- **Format**: Time series buckets

### Panel 7: Active Requests
- **Type**: Gauge
- **Query**: `sum(billing_http_requests_in_progress)`
- **Max**: 100

### Panel 8: Top Endpoints
- **Type**: Table
- **Query**: `topk(10, sum by (endpoint, method) (rate(billing_http_requests_total[5m])))`

## Jaeger Trace Queries

### Find Slow Requests
- **Service**: ciris-billing-api
- **Operation**: All
- **Min Duration**: 1s
- **Limit**: 100

### Find Failed Requests
- **Service**: ciris-billing-api
- **Tags**: `error=true`

### Trace by Request ID
- **Tags**: `request_id=<request-id>`

### Database Query Traces
- **Service**: ciris-billing-api
- **Operation**: Contains "SELECT" or "INSERT"

## LogQL Queries (Loki - if added)

### All Logs from Billing API
```logql
{service="ciris-billing-api"}
```

### Error Logs
```logql
{service="ciris-billing-api"} |= "level=error"
```

### Logs for Specific Request ID
```logql
{service="ciris-billing-api"} |= "request_id=req-123"
```

### Charge Creation Logs
```logql
{service="ciris-billing-api"} |= "charge_creation"
```

### Failed Operations
```logql
{service="ciris-billing-api"} | json | success="False"
```

## Alert Rules

### High Error Rate
```yaml
- alert: HighErrorRate
  expr: |
    sum(rate(billing_http_requests_total{status_code=~"5.."}[1m]))
      /
    sum(rate(billing_http_requests_total[1m]))
    > 0.01
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "High error rate detected"
    description: "Error rate is {{ $value | humanizePercentage }}"
```

### Slow Response Time
```yaml
- alert: SlowResponseTime
  expr: |
    histogram_quantile(0.95,
      sum(rate(billing_http_request_duration_seconds_bucket[5m])) by (le, endpoint)
    ) > 1.0
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Slow response time on {{ $labels.endpoint }}"
    description: "p95 response time is {{ $value }}s"
```

### Database Connection Pool Exhausted
```yaml
- alert: DatabaseConnectionPoolExhausted
  expr: billing_db_connections_active >= 20
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Database connection pool near limit"
    description: "Active connections: {{ $value }}"
```

### High Credit Denial Rate
```yaml
- alert: HighCreditDenialRate
  expr: |
    sum(rate(billing_credit_checks_total{has_credit="False"}[1m]))
      /
    sum(rate(billing_credit_checks_total[1m]))
    > 0.5
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "High credit denial rate"
    description: "{{ $value | humanizePercentage }} of credit checks are denied"
```

## Common Queries by Use Case

### Debugging a Slow Request
1. Find request in Jaeger by trace ID or request ID
2. Examine span durations to find bottleneck
3. Check database query spans for slow queries
4. Review logs for that request ID

### Investigating an Outage
1. Check error rate graph in Grafana
2. Query error logs by time range
3. Check database connection metrics
4. Review traces for failed requests

### Capacity Planning
1. Monitor request rate trends
2. Check p95/p99 response times
3. Review database query duration
4. Track active connections and in-progress requests

### Financial Reconciliation
1. Sum total charges: `sum(increase(billing_charge_amount_minor_sum[24h]))`
2. Sum total credits: `sum(increase(billing_credit_amount_minor_sum[24h]))`
3. Count transactions: `increase(billing_charges_total[24h])`
