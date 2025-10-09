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

**Migration Chain (All 2025-10-08):**
1. `2025_10_08_0000` - Initial schema (accounts, charges, credits, credit_checks)
2. `2025_10_08_0001` - Usage tracking (free_uses_remaining, total_uses)
3. `2025_10_08_0002` - Admin system (api_keys, admin_users, provider_configs, audit_logs)
4. `2025_10_08_0003` - OAuth (remove password auth, add google_id)
5. `2025_10_08_0004` - Marketing consent (GDPR compliance)

---

## Environment Variables Required

```bash
# PostgreSQL
POSTGRES_USER=ciris
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=ciris_billing
DATABASE_URL=postgresql+asyncpg://ciris:<password>@postgres:5432/ciris_billing

# Stripe
STRIPE_API_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PUBLISHABLE_KEY=pk_test_...

# Google OAuth
GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<client-secret>
ADMIN_JWT_SECRET=$(openssl rand -hex 32)

# Observability
LOG_LEVEL=INFO
GRAFANA_PASSWORD=<strong-password>
```

---

## Quick Commands

```bash
# SSH to server
ssh -i ~/.ssh/ciris_deploy root@149.28.120.73

# Deploy
docker-compose -f docker-compose.admin.yml up -d

# Run migrations
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head

# View logs
docker-compose -f docker-compose.admin.yml logs -f billing-api
```
