# CIRIS Billing - Claude Context & Deployment Guide

## Server Access

**Dev Server:**
```bash
ssh -i ~/.ssh/ciris_deploy root@149.28.120.73
```

---

## Project Overview

**CIRIS Billing** - Credit gating service for CIRIS Agent
- **Domain**: billing.ciris.ai
- **Tech Stack**: FastAPI, PostgreSQL, Docker Compose, GitHub Actions CI/CD
- **Test Coverage**: 66% (475 tests)
- **Authentication**:
  - Agent API: API keys (Argon2id) with `billing:read`/`billing:write` permissions
  - Admin UI: Google OAuth (@ciris.ai only)
  - Tool routes: API key auth for service-to-service calls

---

## Architecture

```
┌─────────────────────────────────────────┐
│ Caddy (443/80)                          │
│  ├─ /admin-ui/* → Static UI             │
│  ├─ /admin/* → FastAPI Admin API        │
│  ├─ /v1/billing/* → Billing API         │
│  └─ /v1/tools/* → Tool Credits API      │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ FastAPI App (8000)                      │
│  ├─ Billing API (API key auth)          │
│  ├─ Tool API (API key auth)             │
│  └─ Admin API (JWT auth)                │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ PostgreSQL (5432)                       │
│  ├─ accounts, charges, credits          │
│  ├─ product_inventory, product_usage_log│
│  ├─ api_keys, admin_users               │
│  └─ provider_configs, audit_logs        │
└─────────────────────────────────────────┘
```

---

## Credit Systems

### Two Credit Pools

1. **Main Account Pool** (`accounts.paid_credits`)
   - General-purpose credits for LLM usage
   - Can fall back to this for tool usage

2. **Product Inventory** (`product_inventory` table)
   - Per-product credits (e.g., `web_search`, `image_gen`)
   - Has free tier + paid credits per product
   - Daily refresh of free credits

### Credit Priority (for tool charges)
1. Product free credits (`product_inventory.free_remaining`)
2. Product paid credits (`product_inventory.paid_credits`)
3. **Main pool fallback** (`accounts.paid_credits`) - converted at product price

### Key Files
- `app/services/product_inventory.py` - ProductInventoryService
- `app/api/tool_routes.py` - `/v1/tools/*` endpoints
- `app/services/billing.py` - BillingService for main credits

---

## API Routes

### Tool Routes (`/v1/tools/*`)
- `GET /v1/tools/balance/{product_type}` - Get balance for a product (JWT auth)
- `GET /v1/tools/balance` - Get all product balances (JWT auth)
- `GET /v1/tools/check/{product_type}` - Quick credit check (JWT auth)
- `POST /v1/tools/charge` - Charge for tool usage (API key auth, `billing:write`)

**Charge Request Body:**
```json
{
  "product_type": "web_search",
  "oauth_provider": "oauth:google",
  "external_id": "user@example.com",
  "wa_id": null,
  "tenant_id": null,
  "idempotency_key": "unique-key",
  "request_id": "req-123"
}
```

### Billing Routes (`/v1/billing/*`)
- `POST /v1/billing/check` - Check credit availability
- `POST /v1/billing/charges` - Record usage charge
- `GET /v1/billing/accounts/{id}` - Get account details

---

## Recent Bug Fixes (Dec 2024)

### 1. Web Search Credits Unavailable
**Issue:** Users with 568 `paid_credits` in main account getting "no web search credits available"
**Root Cause:** `product_inventory` table was empty, service only checked product-specific credits
**Fix:** Added main pool fallback to `ProductInventoryService.charge()` and `get_balance()`
**Commit:** `ffa21df`

### 2. /v1/tools/charge 401 Unauthorized
**Issue:** Proxy's service API key rejected by tool charge endpoint
**Root Cause:** Endpoint used JWT-only auth (`get_validated_identity`)
**Fix:** Changed to API key auth (`require_permission("billing:write")`) with identity in request body
**Commit:** `a88a1fb`

### 3. Mypy Export Error
**Issue:** `APIKeyData` not explicitly exported from `app.api.dependencies`
**Fix:** Added `__all__` list for explicit re-exports
**Commit:** `a66e642`

---

## Database Schema

**Migration Chain:**
1. `2025_10_08_0000` - Initial schema
2. `2025_10_08_0001` - Usage tracking
3. `2025_10_08_0002` - Admin system
4. `2025_10_08_0003` - OAuth
5. `2025_10_08_0004` - Marketing consent (GDPR)
6. `2025_10_09_0005` - User metadata
7. `2025_10_15_0006` - Customer email
8. `2025_10_16_0007` - Remove charge balance constraint
9. `2025_10_21_0008` - Add paid_credits field
10. `2025_12_XX_0009` - Product inventory tables

### Key Tables

**accounts:**
- `paid_credits` - Main credit pool (1 credit = 1 API call)
- `free_uses_remaining` - Free tier counter
- `balance_minor` - Reserved for future currency billing

**product_inventory:**
- `account_id`, `product_type`
- `free_remaining`, `paid_credits`
- `last_daily_refresh`, `total_uses`

**product_usage_log:**
- Audit trail for all product charges
- Supports idempotency via `idempotency_key`

---

## Environment Variables & Docker Secrets

**Server credentials:** `/root/.ciris_credentials.txt`

### Docker Secrets
```bash
/var/lib/docker/volumes/cirisbilling_database_url/_data/database_url
/var/lib/docker/volumes/cirisbilling_admin_jwt_secret/_data/admin_jwt_secret
/var/lib/docker/volumes/cirisbilling_encryption_key/_data/encryption_key
/var/lib/docker/volumes/cirisbilling_google_client_secret/_data/google_client_secret
```

### Container Startup
```bash
sh -c 'export DATABASE_URL=$(cat /run/secrets/database_url) && \
       export ADMIN_JWT_SECRET=$(cat /run/secrets/admin_jwt_secret) && \
       export ENCRYPTION_KEY=$(cat /run/secrets/encryption_key) && \
       export GOOGLE_CLIENT_SECRET=$(cat /run/secrets/google_client_secret) && \
       python -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
```

---

## CI/CD

**GitHub Actions Workflows:**
- `CI` - Tests, mypy, linting
- `SonarCloud` - Code quality analysis
- `Build & Push Docker` - ghcr.io/cirisai/cirisbilling
- `Deploy` - Auto-deploy to production on main push

**Pre-commit Hooks:**
- ruff (linting)
- ruff-format
- trailing whitespace, end of files, yaml check

---

## Quick Commands

```bash
# SSH to server
ssh -i ~/.ssh/ciris_deploy root@billing.ciris.ai

# Check service health
curl https://billing.ciris.ai/health

# View logs
docker logs ciris-billing-api --tail 100 -f

# Run migrations
docker exec ciris-billing-api sh -c 'export DATABASE_URL=$(cat /run/secrets/database_url) && alembic upgrade head'

# Access database
docker exec ciris-billing-postgres psql -U ciris -d ciris_billing

# Run tests locally
python -m pytest --tb=short

# Run mypy
python -m mypy app/ --strict
```

---

## CIRIS Covenant Alignment

See `MISSION_ALIGNMENT.md` for full details on how CIRISBilling aligns with the CIRIS Covenant 1.0b.

**Key Principles:**
- Non-maleficence: Safe operations, no user harm
- Beneficence: Enable AI capabilities through credit management
- Transparency: Full audit trail, observable metrics
- Justice: Fair pricing, no discrimination

---

## Related Projects

- **CIRISBridge** - Infrastructure/deployment (Ansible)
  - Ticket `SAGE-001`: Production CIRISManager for SAGE GDPR agent
  - See `/home/emoore/CIRISBridge/docs/tickets/SAGE-001-prod-cirismanager.md`

- **CIRISProxy** - API proxy service (calls billing API for charges)

- **SAGE** - GDPR compliance agent (planned integration)
