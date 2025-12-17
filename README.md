# CIRIS Billing API

A production-grade, horizontally scalable billing service for credit-based usage gating in CIRIS Agent. Designed to replace the Unlimit.com integration with a self-hosted solution featuring PostgreSQL replication, write verification, and zero-dictionary type safety.

**Version:** 0.1.0

## Features

- **Horizontal Scalability**: Stateless API instances with load balancing
- **High Availability**: PostgreSQL primary-replica replication
- **Data Integrity**: Write verification with read-after-write consistency
- **Type Safety**: Zero dictionary usage - full Pydantic + SQLAlchemy typing
- **Idempotency**: All mutations support idempotency keys
- **Containerization**: Complete Docker Compose orchestration
- **Automatic Migrations**: Database migrations run automatically at startup

## Architecture

```
┌─────────────┐
│   Nginx     │  Load Balancer (port 8080)
└──────┬──────┘
       │
       ├─────────┬─────────┬─────────
       │         │         │
   ┌───▼──┐  ┌──▼───┐  ┌──▼───┐
   │ API  │  │ API  │  │ API  │  3x Stateless Instances
   │  #1  │  │  #2  │  │  #3  │
   └───┬──┘  └──┬───┘  └──┬───┘
       │        │         │
       └────────┴─────────┘
                │
         ┌──────▼────────┐
         │   PgBouncer   │  Connection Pooler
         └──────┬────────┘
                │
       ┌────────┴─────────┐
       │                  │
   ┌───▼────────┐    ┌────▼────────┐
   │ PostgreSQL │───▶│ PostgreSQL  │
   │  Primary   │    │   Replica   │
   └────────────┘    └─────────────┘
      (Writes)         (Reads)
```

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development)

### 1. Clone and Configure

```bash
git clone <repository-url>
cd CIRISBilling

# Copy environment template
cp .env.example .env

# Edit .env with your passwords
vim .env
```

### 2. Start Services

```bash
# Start all services (3 API instances, Postgres, PgBouncer, Nginx)
docker-compose up -d

# View logs
docker-compose logs -f
```

### 3. Verify Health

> **Note:** Database migrations run automatically when the application starts. No manual migration step is required.

```bash
# Check API health through load balancer
curl http://localhost:8080/health

# Expected response:
# {
#   "status": "healthy",
#   "database": "connected",
#   "timestamp": "2025-01-08T12:00:00Z"
# }
```

## API Endpoints

### Check Credit

**POST** `/v1/billing/credits/check`

Verify account has sufficient credits before interaction.

```bash
curl -X POST http://localhost:8080/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "wa_id": null,
    "tenant_id": null,
    "context": {
      "agent_id": "datum",
      "channel_id": "discord:123",
      "request_id": "req-456"
    }
  }'
```

**Response 200 OK**:
```json
{
  "has_credit": true,
  "credits_remaining": 5000,
  "plan_name": "pro",
  "reason": null
}
```

### Create Charge

**POST** `/v1/billing/charges`

Deduct credits from account after successful interaction.

```bash
curl -X POST http://localhost:8080/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Agent interaction",
    "idempotency_key": "msg-123",
    "metadata": {
      "message_id": "msg-123",
      "agent_id": "datum",
      "request_id": "req-456"
    }
  }'
```

**Response 201 Created**:
```json
{
  "charge_id": "550e8400-e29b-41d4-a716-446655440000",
  "account_id": "650e8400-e29b-41d4-a716-446655440000",
  "amount_minor": 100,
  "currency": "USD",
  "balance_after": 4900,
  "created_at": "2025-01-08T12:00:00Z",
  "description": "Agent interaction",
  "metadata": {
    "message_id": "msg-123",
    "agent_id": "datum",
    "channel_id": null,
    "request_id": "req-456"
  }
}
```

### Add Credits

**POST** `/v1/billing/credits`

Add credits to account (purchase, grant, refund).

```bash
curl -X POST http://localhost:8080/v1/billing/credits \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "amount_minor": 5000,
    "currency": "USD",
    "description": "Monthly subscription",
    "transaction_type": "purchase",
    "external_transaction_id": "stripe_ch_123",
    "idempotency_key": "stripe_ch_123"
  }'
```

### Create Account

**POST** `/v1/billing/accounts`

Create new account or get existing (upsert).

```bash
curl -X POST http://localhost:8080/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "initial_balance_minor": 1000,
    "currency": "USD",
    "plan_name": "free"
  }'
```

### Get Account

**GET** `/v1/billing/accounts/{oauth_provider}/{external_id}`

Retrieve account details.

```bash
curl http://localhost:8080/v1/billing/accounts/oauth:google/user@example.com
```

## HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | OK | Credit check succeeded |
| 201 | Created | Charge/credit created |
| 402 | Payment Required | Insufficient credits |
| 403 | Forbidden | Account suspended |
| 404 | Not Found | Account doesn't exist |
| 409 | Conflict | Idempotency key conflict |
| 422 | Validation Error | Invalid request data |
| 500 | Internal Error | Database integrity error |
| 503 | Service Unavailable | Database down |

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements-dev.txt

# Set environment variables
export DATABASE_URL="postgresql+asyncpg://billing_admin:password@localhost:5432/ciris_billing"

# Start development server (migrations run automatically)
python -m app.main
```

### Run Tests

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run tests with coverage
pytest --cov=app --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Code Quality

```bash
# Format code
black app/ tests/

# Lint code
ruff check app/ tests/

# Type check
mypy app/
```

## Scaling

### Horizontal Scaling

Scale API instances:

```bash
# Scale to 5 instances
docker-compose up -d --scale billing-api=5

# Nginx automatically load balances across all instances
```

### Database Replication

The system uses PostgreSQL streaming replication:

- **Primary**: Handles all writes (INSERT, UPDATE, DELETE)
- **Replica**: Handles reads (SELECT for credit checks, account lookups)
- **PgBouncer**: Connection pooling (1000 client connections → 25 database connections)

Replication lag is typically <100ms. Monitor with:

```sql
-- On replica
SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;
```

## Monitoring

### Health Checks

```bash
# API health (through load balancer)
curl http://localhost:8080/health

# Individual instance health
curl http://localhost:8000/health  # billing-api-1
```

### Database Connections

```bash
# Check PgBouncer stats
docker-compose exec pgbouncer psql -U billing_admin -p 5432 pgbouncer -c "SHOW STATS;"

# Check active connections
docker-compose exec postgres-primary psql -U billing_admin -d ciris_billing \
  -c "SELECT count(*) FROM pg_stat_activity;"
```

### Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f billing-api-1
docker-compose logs -f postgres-primary
docker-compose logs -f nginx
```

## Database Schema

### Tables

- **accounts**: User accounts with balance and status
- **charges**: Immutable ledger of credit deductions
- **credits**: Immutable ledger of credit additions
- **credit_checks**: Audit log of all credit check requests

### Key Constraints

- `accounts.balance_minor >= 0` (non-negative balance)
- `charges.balance_after = balance_before - amount_minor` (balance consistency)
- `credits.balance_after = balance_before + amount_minor` (balance consistency)
- Unique constraint on `(oauth_provider, external_id)` - prevents duplicate accounts
- Legacy unique constraint on `(oauth_provider, external_id, wa_id, tenant_id)` for backwards compatibility

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (required) | Primary database connection URL |
| `DATABASE_READ_URL` | (optional) | Replica database URL (falls back to primary) |
| `API_PORT` | `8000` | API server port |
| `API_HOST` | `0.0.0.0` | API server host |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARN, ERROR) |
| `DB_PASSWORD` | (required) | Database password |
| `REPLICATION_PASSWORD` | (required) | Replication user password |

## Write Verification

All write operations verify data integrity:

```python
# Pattern for all writes
async def create_charge(...):
    # 1. Insert
    db.add(charge)
    await db.flush()

    # 2. Verify - read back
    verified = await db.get(Charge, charge.id)
    if verified is None:
        raise WriteVerificationError()

    # 3. Validate invariants
    if verified.balance_after != expected:
        raise DataIntegrityError()

    return verified
```

This ensures:
- No silent write failures
- Balance consistency maintained
- Race conditions detected

## Type Safety

Zero dictionary usage - all data structures are typed:

```python
# ✅ Correct - Strongly typed
account = AccountIdentity(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    wa_id=None,
    tenant_id=None
)

# ❌ Wrong - Never use dicts
account = {
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com"
}
```

## Security

- **SQL Injection**: Protected by SQLAlchemy ORM parameterization
- **Rate Limiting**: Nginx rate limiting (100 req/s per IP)
- **Connection Pooling**: PgBouncer prevents connection exhaustion
- **TLS**: Enable HTTPS in production (Nginx SSL termination)

## Backup & Recovery

### Backup

```bash
# Backup primary database
docker-compose exec postgres-primary pg_dump -U billing_admin ciris_billing \
  | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz
```

### Restore

```bash
# Restore from backup
gunzip -c backup_20250108_120000.sql.gz | \
  docker-compose exec -T postgres-primary psql -U billing_admin ciris_billing
```

## Troubleshooting

### API returns 503 Service Unavailable

Check database connectivity:

```bash
docker-compose logs postgres-primary
docker-compose exec billing-api-1 psql -U billing_admin -h postgres-primary -d ciris_billing -c "SELECT 1;"
```

### Charges failing with DataIntegrityError

Check balance consistency:

```sql
SELECT
  a.id,
  a.balance_minor as current_balance,
  COALESCE(SUM(cr.amount_minor), 0) - COALESCE(SUM(ch.amount_minor), 0) as calculated_balance
FROM accounts a
LEFT JOIN credits cr ON cr.account_id = a.id
LEFT JOIN charges ch ON ch.account_id = a.id
GROUP BY a.id, a.balance_minor
HAVING a.balance_minor != COALESCE(SUM(cr.amount_minor), 0) - COALESCE(SUM(ch.amount_minor), 0);
```

### Replication lag too high

Check replication status:

```sql
-- On primary
SELECT * FROM pg_stat_replication;

-- On replica
SELECT now() - pg_last_xact_replay_timestamp() AS lag;
```

## Integration with CIRIS Agent

Replace `UnlimitCreditProvider` in CIRISAgent with:

```python
from ciris_engine.logic.services.infrastructure.resource_monitor import CreditGateProtocol

class CirisBillingProvider(CreditGateProtocol):
    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url

    async def check_credit(self, account, context) -> CreditCheckResult:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/credits/check",
                json={
                    "oauth_provider": account.provider,
                    "external_id": account.account_id,
                    "wa_id": account.authority_id,
                    "tenant_id": account.tenant_id,
                    "context": {
                        "agent_id": context.agent_id,
                        "channel_id": context.channel_id,
                        "request_id": context.request_id,
                    }
                }
            )
            data = response.json()
            return CreditCheckResult(
                has_credit=data["has_credit"],
                credits_remaining=data.get("credits_remaining"),
                reason=data.get("reason"),
                plan_name=data.get("plan_name"),
            )

    async def spend_credit(self, account, request, context) -> CreditSpendResult:
        # Similar implementation for charges endpoint
        pass
```

## License

See [LICENSE](LICENSE) file.

## Support

For issues and questions, see [claude.md](claude.md) for detailed design documentation.
