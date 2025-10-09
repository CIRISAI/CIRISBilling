# Local Testing Guide

Complete guide to running and testing the CIRIS Billing API locally with test data.

## Quick Start

```bash
# 1. Start complete local stack (API + Database + Observability)
make test-local

# 2. Run end-to-end tests
make test-e2e

# 3. Stop when done
make test-local-stop
```

That's it! You now have a fully functional billing API with test data.

---

## What Gets Started

The `make test-local` command starts:

### Core Services
- **PostgreSQL** - Database with pre-loaded test data
- **Billing API** - FastAPI application
- **Alembic Migrations** - Automatic schema setup

### Observability Stack
- **OpenTelemetry Collector** - Telemetry aggregation
- **Jaeger** - Distributed tracing UI
- **Prometheus** - Metrics storage
- **Grafana** - Visualization dashboards

### Test Data
- **5 test accounts** with varying balances
- **Historical charges** and **credits**
- **Audit logs** for testing queries

---

## Test Accounts

| Email | OAuth Provider | Balance | Status | Use Case |
|-------|----------------|---------|--------|----------|
| `test-user-1@example.com` | oauth:google | 5,000 | active | Normal usage |
| `test-user-2@example.com` | oauth:google | 50 | active | Low balance |
| `discord-user-123456` | oauth:discord | 0 | active | Zero balance |
| `suspended-user@example.com` | oauth:google | 1,000 | suspended | Suspended account |
| `whale-user@example.com` | oauth:google | 100,000 | active | High-volume user |

---

## Accessing Services

### API Endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| **API** | http://localhost:8000 | REST API |
| **Health** | http://localhost:8000/health | Health check |
| **Metrics** | http://localhost:8000/metrics | Prometheus metrics |
| **Docs** | http://localhost:8000/docs | Interactive API documentation |

### Observability

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | admin / admin |
| **Prometheus** | http://localhost:9090 | None |
| **Jaeger** | http://localhost:16686 | None |

### Database

```bash
# Direct connection
psql -h localhost -p 5432 -U billing_admin -d ciris_billing
# Password: testpass123

# Via Docker
docker-compose -f docker-compose.local.yml exec postgres psql -U billing_admin -d ciris_billing
```

---

## Running Tests

### Option 1: Bash Tests (Fast)

```bash
make test-e2e
```

**Output:**
```
✓ Health check
✓ Get existing account
✓ Credit check - sufficient balance
✓ Credit check - zero balance
✓ Create charge - success
✓ Create charge - idempotency conflict
✓ Create charge - insufficient balance
✓ Add credits - success

Passed: 15
Failed: 0
```

### Option 2: Python/Pytest Tests (Detailed)

```bash
make test-e2e-python
```

**Output:**
```
tests/e2e/test_api_endpoints.py::TestHealthAndMetrics::test_health_check PASSED
tests/e2e/test_api_endpoints.py::TestAccountOperations::test_get_existing_account PASSED
tests/e2e/test_api_endpoints.py::TestCreditChecks::test_credit_check_sufficient_balance PASSED
...
==================== 20 passed in 2.35s ====================
```

---

## Manual Testing

### Check Credit

```bash
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "wa_id": "wa-test-001",
    "tenant_id": "tenant-acme",
    "context": {}
  }'
```

**Expected Response:**
```json
{
  "has_credit": true,
  "credits_remaining": 5000,
  "plan_name": "pro",
  "reason": null
}
```

### Create Charge

```bash
curl -X POST http://localhost:8000/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "wa_id": "wa-test-001",
    "tenant_id": "tenant-acme",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Test charge",
    "metadata": {
      "message_id": "test-001",
      "agent_id": "datum"
    }
  }'
```

**Expected Response:**
```json
{
  "charge_id": "550e8400-...",
  "account_id": "550e8400-...",
  "amount_minor": 100,
  "currency": "USD",
  "balance_after": 4900,
  "created_at": "2025-01-08T12:34:56.789Z",
  "description": "Test charge",
  "metadata": {...}
}
```

### Add Credits

```bash
curl -X POST http://localhost:8000/v1/billing/credits \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:discord",
    "external_id": "discord-user-123456",
    "amount_minor": 1000,
    "currency": "USD",
    "description": "Test credit addition",
    "transaction_type": "grant"
  }'
```

### Get Account

```bash
curl "http://localhost:8000/v1/billing/accounts/oauth:google/test-user-1@example.com?wa_id=wa-test-001&tenant_id=tenant-acme"
```

### Create Account

```bash
curl -X POST http://localhost:8000/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "new-user@example.com",
    "initial_balance_minor": 5000,
    "currency": "USD",
    "plan_name": "free"
  }'
```

---

## Viewing Telemetry

### Logs

```bash
# Follow logs from API
docker-compose -f docker-compose.local.yml logs -f billing-api

# View structured JSON logs
docker-compose -f docker-compose.local.yml logs billing-api | jq

# Filter by event
docker-compose -f docker-compose.local.yml logs billing-api | jq 'select(.event == "charge_created")'
```

### Metrics

**View in Prometheus** (http://localhost:9090):

```promql
# Request rate
rate(billing_http_requests_total[1m])

# Error rate
sum(rate(billing_http_requests_total{status_code=~"5.."}[1m]))
  / sum(rate(billing_http_requests_total[1m]))

# p95 latency
histogram_quantile(0.95,
  sum(rate(billing_http_request_duration_seconds_bucket[5m])) by (le)
)

# Credit check rate
rate(billing_credit_checks_total[1m])

# Charge success rate
sum(rate(billing_charges_total{success="True"}[1m]))
  / sum(rate(billing_charges_total[1m]))
```

### Traces

**View in Jaeger** (http://localhost:16686):

1. Select Service: `ciris-billing-api-test`
2. Click "Find Traces"
3. Click on any trace to see spans

**Example trace structure:**
```
POST /v1/billing/charges (42ms)
├─ BillingService.create_charge (35ms)
│  ├─ SELECT account FOR UPDATE (8ms)
│  ├─ INSERT INTO charges (12ms)
│  ├─ UPDATE accounts (10ms)
│  └─ SELECT verification (3ms)
└─ Serialization (2ms)
```

---

## Database Queries

### View Test Accounts

```sql
SELECT
    external_id,
    balance_minor,
    currency,
    plan_name,
    status
FROM accounts
ORDER BY balance_minor DESC;
```

### View Recent Charges

```sql
SELECT
    a.external_id,
    c.amount_minor,
    c.balance_before,
    c.balance_after,
    c.description,
    c.created_at
FROM charges c
JOIN accounts a ON c.account_id = a.id
ORDER BY c.created_at DESC
LIMIT 10;
```

### Check Balance Integrity

```sql
-- Verify balance = credits - charges
SELECT
    a.id,
    a.external_id,
    a.balance_minor as current_balance,
    COALESCE(SUM(cr.amount_minor), 0) - COALESCE(SUM(ch.amount_minor), 0) as calculated_balance,
    a.balance_minor - (COALESCE(SUM(cr.amount_minor), 0) - COALESCE(SUM(ch.amount_minor), 0)) as difference
FROM accounts a
LEFT JOIN credits cr ON cr.account_id = a.id
LEFT JOIN charges ch ON ch.account_id = a.id
GROUP BY a.id, a.external_id, a.balance_minor
HAVING a.balance_minor != COALESCE(SUM(cr.amount_minor), 0) - COALESCE(SUM(ch.amount_minor), 0);
```

### View Audit Logs

```sql
SELECT
    external_id,
    has_credit,
    credits_remaining,
    denial_reason,
    created_at
FROM credit_checks
ORDER BY created_at DESC
LIMIT 10;
```

---

## Resetting Test Data

### Full Reset

```bash
# Stop and remove all data
make test-local-stop

# Start fresh with new test data
make test-local
```

### Reload Test Data Only

```bash
# Rerun the test data SQL script
docker-compose -f docker-compose.local.yml exec postgres \
  psql -U billing_admin -d ciris_billing \
  -f /docker-entrypoint-initdb.d/99-test-data.sql
```

### Delete Specific Account

```sql
-- Delete account and all related records
DELETE FROM credit_checks WHERE account_id = (
    SELECT id FROM accounts WHERE external_id = 'test-user-1@example.com'
);

DELETE FROM charges WHERE account_id = (
    SELECT id FROM accounts WHERE external_id = 'test-user-1@example.com'
);

DELETE FROM credits WHERE account_id = (
    SELECT id FROM accounts WHERE external_id = 'test-user-1@example.com'
);

DELETE FROM accounts WHERE external_id = 'test-user-1@example.com';
```

---

## Troubleshooting

### API Not Starting

```bash
# Check logs
docker-compose -f docker-compose.local.yml logs billing-api

# Check database connection
docker-compose -f docker-compose.local.yml exec billing-api \
  python -c "import asyncpg; print('OK')"
```

### Database Connection Failed

```bash
# Verify database is running
docker-compose -f docker-compose.local.yml ps

# Check database health
docker-compose -f docker-compose.local.yml exec postgres \
  pg_isready -U billing_admin
```

### Test Data Not Loaded

```bash
# Manually load test data
docker-compose -f docker-compose.local.yml exec postgres \
  psql -U billing_admin -d ciris_billing \
  -f /docker-entrypoint-initdb.d/99-test-data.sql

# Verify account count
docker-compose -f docker-compose.local.yml exec postgres \
  psql -U billing_admin -d ciris_billing \
  -c "SELECT COUNT(*) FROM accounts;"
```

### Tests Failing

```bash
# Check API is healthy
curl http://localhost:8000/health

# View recent logs
docker-compose -f docker-compose.local.yml logs --tail=50 billing-api

# Check test data exists
curl "http://localhost:8000/v1/billing/accounts/oauth:google/test-user-1@example.com?wa_id=wa-test-001&tenant_id=tenant-acme"
```

### Port Conflicts

```bash
# Stop other services using ports 8000, 5432, 3000, 9090, 16686
lsof -i :8000
lsof -i :5432

# Or use different ports in docker-compose.local.yml
```

---

## Performance Testing

### Load Test with Apache Bench

```bash
# Install apache-bench
sudo apt-get install apache2-utils  # Ubuntu
brew install apache-bench            # macOS

# Test credit check endpoint (100 requests, 10 concurrent)
ab -n 100 -c 10 -p credit-check.json -T application/json \
  http://localhost:8000/v1/billing/credits/check
```

**credit-check.json:**
```json
{
  "oauth_provider": "oauth:google",
  "external_id": "test-user-1@example.com",
  "wa_id": "wa-test-001",
  "tenant_id": "tenant-acme",
  "context": {}
}
```

### Monitor Performance

```bash
# Watch metrics in real-time
watch -n 1 'curl -s http://localhost:8000/metrics | grep billing_http_requests'

# View in Grafana
# Open http://localhost:3000 and create dashboard with:
# - Request rate: rate(billing_http_requests_total[1m])
# - Latency: histogram_quantile(0.95, ...)
# - Error rate: sum(rate(...{status_code=~"5.."}[1m]))
```

---

## Integration with CIRIS Agent

To test with actual CIRIS Agent:

```bash
# 1. Update CIRIS Agent configuration to point to local API
export BILLING_API_URL=http://localhost:8000

# 2. Create test account
curl -X POST http://localhost:8000/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "agent-test-user@example.com",
    "initial_balance_minor": 10000,
    "currency": "USD",
    "plan_name": "test"
  }'

# 3. Run CIRIS Agent with test user credentials
# Agent will automatically check credits and create charges
```

---

## Continuous Integration

For CI/CD pipelines:

```bash
#!/bin/bash
# ci-test.sh

set -e

# Start local stack
./test-local.sh

# Wait for services
sleep 5

# Run tests
./tests/e2e/run-tests.sh

# Capture exit code
EXIT_CODE=$?

# Cleanup
docker-compose -f docker-compose.local.yml down

exit $EXIT_CODE
```

---

## Summary

| Command | Purpose |
|---------|---------|
| `make test-local` | Start complete local testing stack |
| `make test-local-stop` | Stop local testing stack |
| `make test-e2e` | Run bash-based E2E tests |
| `make test-e2e-python` | Run pytest-based E2E tests |
| `docker-compose -f docker-compose.local.yml logs -f` | View logs |
| `docker-compose -f docker-compose.local.yml ps` | Check status |

**Test Accounts**: 5 accounts with varying balances (0 to 100,000)
**Services**: API + Database + Observability (Grafana, Prometheus, Jaeger)
**Test Data**: Pre-loaded charges, credits, and audit logs

🎉 **You now have a complete local testing environment!**
