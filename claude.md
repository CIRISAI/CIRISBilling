# CIRIS Billing - Claude Context & Deployment Guide

## Server Access

**Dev Server:**
```bash
ssh -i ~/.ssh/ciris_deploy root@149.28.120.73
```

---

## Project Overview

**CIRIS Billing** - Credit gating service for CIRIS Agent
- **Domain**: billing.ciris.ai (will need DNS setup)
- **Tech Stack**: FastAPI, PostgreSQL, Nginx, Docker Compose
- **Authentication**:
  - Agent API: API keys (Argon2id)
  - Admin UI: Google OAuth (@ciris.ai only)

---

## Architecture

```
┌─────────────────────────────────────────┐
│ Nginx (443/80)                          │
│  ├─ /admin → Static UI                  │
│  ├─ /admin/oauth/* → FastAPI OAuth      │
│  ├─ /admin/api/* → FastAPI Admin API    │
│  └─ /v1/billing/* → FastAPI Billing API │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ FastAPI App (8000)                      │
│  ├─ Billing API (API key auth)          │
│  └─ Admin API (JWT auth)                │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ PostgreSQL (5432)                       │
│  ├─ accounts, charges, credits          │
│  ├─ api_keys, admin_users               │
│  └─ provider_configs, audit_logs        │
└─────────────────────────────────────────┘

Observability:
- Jaeger (16686) - Distributed tracing
- Prometheus (9090) - Metrics
- Grafana (3000) - Dashboards
```

---

## Nginx Routing Configuration

**IMPORTANT**: The nginx reverse proxy routes different paths to different destinations:

### Static Files (Admin UI)
- `/ ` → serves `/login.html` (root redirects to login)
- `/admin/*` → serves static files from `/usr/share/nginx/html/admin/`
- Examples:
  - `/admin/dashboard.html` → static file
  - `/admin/dashboard.js` → static file

### API Endpoints (Proxied to FastAPI Backend)

**OAuth Endpoints** (no `/api/` prefix):
- `/admin/oauth/login` → proxied to FastAPI `/admin/oauth/login`
- `/admin/oauth/callback` → proxied to FastAPI `/admin/oauth/callback`
- `/admin/oauth/user` → proxied to FastAPI `/admin/oauth/user`

**Admin API Endpoints** (requires `/api/` prefix):
- `/admin/api/*` → proxied to FastAPI `/admin/*`
- Examples:
  - `/admin/api/analytics/overview` → FastAPI `/admin/analytics/overview`
  - `/admin/api/users` → FastAPI `/admin/users`
  - `/admin/api/api-keys` → FastAPI `/admin/api-keys`
  - `/admin/api/config/providers/stripe` → FastAPI `/admin/config/providers/stripe`

**Billing API Endpoints** (for agents):
- `/v1/billing/*` → proxied to FastAPI `/v1/billing/*`

### JavaScript API Calls

When making API calls from dashboard.js, use:
- OAuth: `/admin/oauth/*` (no `/api/`)
- Admin API: `/admin/api/*` (with `/api/`)

**Example:**
```javascript
// OAuth - no /api/ prefix
fetch('/admin/oauth/user', { headers: { Authorization: `Bearer ${token}` } })

// Admin API - with /api/ prefix
fetch('/admin/api/analytics/overview', { headers: { Authorization: `Bearer ${token}` } })
fetch('/admin/api/api-keys', { method: 'POST', ... })
```

---

## Database Schema

**Migration Chain:**
1. `2025_10_08_0000` - Initial schema (accounts, charges, credits, credit_checks)
2. `2025_10_08_0001` - Usage tracking (free_uses_remaining, total_uses)
3. `2025_10_08_0002` - Admin system (api_keys, admin_users, provider_configs, audit_logs)
4. `2025_10_08_0003` - OAuth (remove password auth, add google_id)
5. `2025_10_08_0004` - Marketing consent (GDPR compliance)
6. `2025_10_09_0005` - User metadata (user_role, agent_id)
7. `2025_10_15_0006` - Customer email field
8. `2025_10_16_0007` - Remove charge balance consistency constraint
9. `2025_10_21_0008` - **Add paid_credits field** (architectural change)

### Credits vs Currency Architecture

**IMPORTANT:** As of migration `2025_10_21_0008`, the system now separates:

- **`paid_credits`** (BigInteger): Tracks purchased usage credits (1 credit = 1 API call)
  - Used for all credit operations (purchases, grants, charges)
  - Migrated from old `balance_minor` values
  - Current balances:
    - smartframe.ai@googlemail.com: 50 credits
    - trent@topbrand-consulting.com: 50 credits
    - mooreericnyc@gmail.com: 19 credits

- **`balance_minor`** (BigInteger): Reserved for future actual currency balance
  - Currently reset to 0 for all accounts
  - Will be used when we add currency-based billing (USD, EUR, etc.)
  - Not used by current billing operations

- **`free_uses_remaining`** (BigInteger): Free tier usage counter
  - Each new account starts with 3 free uses (configurable)
  - Decremented before charging paid_credits
  - Independent of paid credits

---

## Environment Variables & Docker Secrets

**Current Production Setup:**

Server credentials are stored in `/root/.ciris_credentials.txt` on the server.

### Docker Secrets (File-based)

Secrets are mounted from Docker volumes into containers at `/run/secrets/`:

```bash
# Secret files locations:
/var/lib/docker/volumes/cirisbilling_database_url/_data/database_url
/var/lib/docker/volumes/cirisbilling_admin_jwt_secret/_data/admin_jwt_secret
/var/lib/docker/volumes/cirisbilling_encryption_key/_data/encryption_key
/var/lib/docker/volumes/cirisbilling_google_client_secret/_data/google_client_secret
```

**DATABASE_URL Format:**
```
postgresql+asyncpg://ciris:<URL_ENCODED_PASSWORD>@ciris-billing-postgres:5432/ciris_billing
```
Note: Password contains `=` which must be URL-encoded as `%3D`

### Container Startup Command

The billing API container exports secrets as environment variables on startup:

```bash
sh -c 'export DATABASE_URL=$(cat /run/secrets/database_url) && \
       export ADMIN_JWT_SECRET=$(cat /run/secrets/admin_jwt_secret) && \
       export ENCRYPTION_KEY=$(cat /run/secrets/encryption_key) && \
       export GOOGLE_CLIENT_SECRET=$(cat /run/secrets/google_client_secret) && \
       python -m uvicorn app.main:app --host 0.0.0.0 --port 8000'
```

### Environment Variables

```bash
# PostgreSQL
POSTGRES_USER=ciris
POSTGRES_PASSWORD=<from-credentials-file>
POSTGRES_DB=ciris_billing

# Stripe (stored in provider_configs table, not env vars)
STRIPE_API_KEY=sk_test_placeholder  # Fallback only
STRIPE_WEBHOOK_SECRET=whsec_placeholder  # Fallback only
STRIPE_PUBLISHABLE_KEY=pk_test_placeholder  # Fallback only

# Google OAuth
GOOGLE_CLIENT_ID=265882853697-vsrucm66hl39jei1f5f20kb49om8t9pb.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<from-docker-secret>

# App Config
ENVIRONMENT=production
LOG_LEVEL=INFO
```

---

## Quick Commands

```bash
# SSH to server
ssh -i ~/.ssh/ciris_deploy root@billing.ciris.ai
# Alternative: ssh -i ~/.ssh/ciris_deploy root@149.28.120.73

# Check service health
curl https://billing.ciris.ai/health

# View running containers
docker ps --filter name=billing

# View logs
docker logs ciris-billing-api --tail 100 -f

# Run migrations
docker exec ciris-billing-api sh -c 'export DATABASE_URL=$(cat /run/secrets/database_url) && alembic upgrade head'

# Check current migration version
docker exec ciris-billing-api sh -c 'export DATABASE_URL=$(cat /run/secrets/database_url) && alembic current'

# Access database
docker exec ciris-billing-postgres psql -U ciris -d ciris_billing

# Rebuild and deploy new code
cd /root/CIRISBilling
docker compose build
docker stop ciris-billing-api && docker rm ciris-billing-api
# Then start with proper command (see Container Startup Command section)

# Restart service
docker restart ciris-billing-api
```

## Deployment Process

1. **Sync code to server:**
   ```bash
   rsync -avz -e "ssh -i ~/.ssh/ciris_deploy" \
     --include='alembic/***' \
     --include='app/***' \
     --include='*.py' \
     --exclude='*' \
     /local/path/CIRISBilling/ root@billing.ciris.ai:/root/CIRISBilling/
   ```

2. **Rebuild Docker image:**
   ```bash
   ssh -i ~/.ssh/ciris_deploy root@billing.ciris.ai "cd /root/CIRISBilling && docker compose build"
   ```

3. **Stop and remove old container:**
   ```bash
   ssh -i ~/.ssh/ciris_deploy root@billing.ciris.ai "docker stop ciris-billing-api && docker rm ciris-billing-api"
   ```

4. **Start new container** (with secret exports - see Container Startup Command section)

5. **Run migrations:**
   ```bash
   ssh -i ~/.ssh/ciris_deploy root@billing.ciris.ai "docker exec ciris-billing-api sh -c 'export DATABASE_URL=\$(cat /run/secrets/database_url) && alembic upgrade head'"
   ```

6. **Verify health:**
   ```bash
   curl https://billing.ciris.ai/health
   ```
