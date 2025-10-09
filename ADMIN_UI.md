# CIRIS Billing - Admin UI Architecture

**Version:** 1.0
**Date:** 2025-10-08
**Status:** Design Document

---

## Table of Contents

1. [Overview](#overview)
2. [API Key Issuance Model](#api-key-issuance-model)
3. [Admin Authentication](#admin-authentication)
4. [Database Schema](#database-schema)
5. [Admin API Endpoints](#admin-api-endpoints)
6. [UI Components](#ui-components)
7. [Security Considerations](#security-considerations)
8. [Implementation Plan](#implementation-plan)

---

## Overview

The CIRIS Billing Admin UI provides a comprehensive management interface for:

- **API Key Management**: Issue, rotate, revoke agent API keys
- **User Analytics**: View all users, usage patterns, purchases
- **Revenue Analytics**: Aggregate metrics (daily, weekly, monthly, all-time)
- **Provider Configuration**: Manage Stripe and other payment providers
- **System Configuration**: PostgreSQL settings, rate limits, pricing

### Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                     Admin Web UI                             │
│  (React SPA - Dashboard, Users, Keys, Config, Analytics)    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ├─ HTTPS/JWT Auth
                            │
┌─────────────────────────────────────────────────────────────┐
│                    Admin API (FastAPI)                       │
│  /admin/api-keys, /admin/users, /admin/analytics, /admin/config │
└─────────────────────────────────────────────────────────────┘
                            │
                            ├─ Database queries
                            │
┌─────────────────────────────────────────────────────────────┐
│                PostgreSQL Database                           │
│  (accounts, charges, credits, api_keys, admin_users)        │
└─────────────────────────────────────────────────────────────┘
```

### Key Separation

```
Agent API Key (X-API-Key)     ──►  /v1/billing/* endpoints  (agent access)
Admin JWT Token (Bearer)      ──►  /admin/* endpoints       (admin access)
```

---

## API Key Issuance Model

### Key Types

**Agent API Key**: Grants access to billing API for a specific agent instance

- **Format**: `cbk_live_<32-char-base64>` (production) or `cbk_test_<32-char-base64>` (testing)
- **Example**: `cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r`
- **Storage**: Hashed using Argon2id (never stored in plaintext)
- **Expiry**: Optional expiration (90 days, 1 year, never)
- **Permissions**: Scoped permissions (read_only, read_write, admin)

### Key Lifecycle

```
┌──────────────┐
│   Created    │  Admin generates key via UI
└──────┬───────┘
       │
       ├──► Key displayed ONCE (plaintext)
       │    Admin copies and stores securely
       │
       ├──► Hash stored in database (Argon2id)
       │
┌──────▼───────┐
│    Active    │  Agent uses key in X-API-Key header
└──────┬───────┘
       │
       ├──► Validated on each request
       ├──► last_used_at timestamp updated
       │
       ├──► [Optional] Admin rotates key
       │         ├──► New key generated
       │         ├──► Old key marked "rotating" (grace period: 24h)
       │         └──► Old key auto-revoked after grace period
       │
┌──────▼───────┐
│   Revoked    │  Admin manually revokes or auto-expired
└──────────────┘
       │
       └──► Returns 401 Unauthorized
```

### Key Metadata

Each API key stores:

```python
class APIKey:
    id: UUID
    key_hash: str                 # Argon2id hash of actual key
    key_prefix: str               # "cbk_live_8f4d..." (first 12 chars, for display)
    name: str                     # "Production Agent", "Staging Agent"
    description: str | None       # "Main production instance"
    environment: Literal["test", "live"]
    permissions: List[str]        # ["billing:read", "billing:write"]
    created_by: UUID              # Admin user who created it
    created_at: datetime
    expires_at: datetime | None   # Optional expiration
    last_used_at: datetime | None # Last request timestamp
    last_used_ip: str | None      # Last IP address
    status: Literal["active", "rotating", "revoked"]
    metadata: dict                # Flexible JSON metadata
```

### Key Generation Algorithm

```python
import secrets
import base64
from argon2 import PasswordHasher

def generate_api_key(environment: str = "live") -> tuple[str, str]:
    """Generate API key and return (plaintext_key, hash)."""

    # Generate cryptographically secure random bytes
    random_bytes = secrets.token_bytes(32)

    # Encode as URL-safe base64
    key_suffix = base64.urlsafe_b64encode(random_bytes).decode('utf-8').rstrip('=')

    # Format: cbk_{env}_{suffix}
    plaintext_key = f"cbk_{environment}_{key_suffix}"

    # Hash for storage (Argon2id)
    ph = PasswordHasher()
    key_hash = ph.hash(plaintext_key)

    return plaintext_key, key_hash
```

### Key Validation

```python
async def validate_api_key(provided_key: str) -> APIKey | None:
    """Validate API key and return key metadata if valid."""

    # Extract prefix for lookup (cbk_live_xxxx or cbk_test_xxxx)
    if not provided_key.startswith("cbk_"):
        return None

    key_prefix = provided_key[:20]  # First 20 chars

    # Look up by prefix (indexed column)
    api_key = await db.query(APIKey).filter(
        APIKey.key_prefix == key_prefix,
        APIKey.status == "active"
    ).first()

    if not api_key:
        return None

    # Verify hash
    ph = PasswordHasher()
    try:
        ph.verify(api_key.key_hash, provided_key)
    except:
        return None

    # Check expiration
    if api_key.expires_at and datetime.now(timezone.utc) > api_key.expires_at:
        api_key.status = "revoked"
        await db.commit()
        return None

    # Update last_used metadata (async, non-blocking)
    asyncio.create_task(update_last_used(api_key.id))

    return api_key
```

### Permissions Model

**Scoped Permissions:**

- `billing:read` - Check credits, get accounts (read-only)
- `billing:write` - Create charges, add credits
- `billing:admin` - Create accounts, update balances
- `analytics:read` - View usage statistics

**Default Agent Key**: `["billing:read", "billing:write"]`

---

## Admin Authentication

### Admin User Model

Separate from agent API keys - admins authenticate via username/password.

```python
class AdminUser:
    id: UUID
    email: str                    # Unique
    password_hash: str            # Argon2id
    full_name: str
    role: Literal["super_admin", "admin", "viewer"]
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    mfa_enabled: bool
    mfa_secret: str | None        # TOTP secret (encrypted)
```

### Roles & Permissions

| Role | API Keys | Users | Config | Analytics |
|------|----------|-------|--------|-----------|
| **viewer** | View | View | View | View |
| **admin** | Full CRUD | View | Edit | View |
| **super_admin** | Full CRUD | Full CRUD | Full CRUD | View |

### Authentication Flow

```
┌─────────────┐
│  Admin UI   │
└─────┬───────┘
      │
      ├──► POST /admin/auth/login
      │    { "email": "admin@example.com", "password": "..." }
      │
┌─────▼───────┐
│  Verify     │  Check password hash + MFA (if enabled)
│  Password   │
└─────┬───────┘
      │
      ├──► Generate JWT token
      │    { "sub": user_id, "role": "admin", "exp": ... }
      │
┌─────▼───────┐
│  Return     │  { "token": "eyJ...", "expires_in": 3600 }
│  JWT        │
└─────┬───────┘
      │
      ├──► Admin stores token in localStorage
      │
┌─────▼───────┐
│  Subsequent │  Authorization: Bearer eyJ...
│  Requests   │
└─────────────┘
```

### JWT Claims

```json
{
  "sub": "550e8400-e29b-41d4-a716-446655440000",
  "email": "admin@example.com",
  "role": "admin",
  "iat": 1704744000,
  "exp": 1704747600
}
```

### MFA (Optional)

- TOTP-based (Google Authenticator, Authy)
- QR code generation on first setup
- Required for super_admin role

---

## Database Schema

### New Tables

#### `api_keys` Table

```sql
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    key_hash TEXT NOT NULL,
    key_prefix VARCHAR(20) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    environment VARCHAR(10) NOT NULL CHECK (environment IN ('test', 'live')),
    permissions TEXT[] NOT NULL DEFAULT ARRAY['billing:read', 'billing:write'],
    created_by UUID NOT NULL REFERENCES admin_users(id),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    last_used_ip INET,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rotating', 'revoked')),
    metadata JSONB DEFAULT '{}',

    CONSTRAINT uk_api_keys_prefix UNIQUE (key_prefix)
);

CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix) WHERE status = 'active';
CREATE INDEX idx_api_keys_created_by ON api_keys(created_by);
CREATE INDEX idx_api_keys_status ON api_keys(status);
```

#### `admin_users` Table

```sql
CREATE TABLE admin_users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer' CHECK (role IN ('super_admin', 'admin', 'viewer')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMP WITH TIME ZONE,
    mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    mfa_secret TEXT,

    CONSTRAINT ck_admin_users_email CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}$')
);

CREATE INDEX idx_admin_users_email ON admin_users(email);
CREATE INDEX idx_admin_users_role ON admin_users(role);
```

#### `provider_configs` Table

```sql
CREATE TABLE provider_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_type VARCHAR(50) NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    config_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_by UUID REFERENCES admin_users(id),

    CONSTRAINT ck_provider_type CHECK (provider_type IN ('stripe', 'square', 'paypal'))
);

-- Example provider_configs row
INSERT INTO provider_configs (provider_type, config_data) VALUES
('stripe', '{
    "api_key": "sk_live_...",
    "webhook_secret": "whsec_...",
    "publishable_key": "pk_live_..."
}');
```

#### `admin_audit_logs` Table

```sql
CREATE TABLE admin_audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_user_id UUID REFERENCES admin_users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(255),
    changes JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_admin_audit_logs_user ON admin_audit_logs(admin_user_id);
CREATE INDEX idx_admin_audit_logs_created_at ON admin_audit_logs(created_at DESC);
```

### Analytics Views

Create materialized views for fast analytics:

```sql
-- Daily aggregates
CREATE MATERIALIZED VIEW daily_analytics AS
SELECT
    DATE(created_at) as date,
    COUNT(DISTINCT account_id) as unique_users,
    COUNT(*) as total_charges,
    SUM(amount_minor) as total_revenue_minor,
    AVG(amount_minor) as avg_charge_minor
FROM charges
GROUP BY DATE(created_at)
ORDER BY date DESC;

CREATE UNIQUE INDEX ON daily_analytics(date);

-- Refresh daily
CREATE OR REPLACE FUNCTION refresh_daily_analytics()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY daily_analytics;
END;
$$ LANGUAGE plpgsql;
```

---

## Admin API Endpoints

All admin endpoints require `Authorization: Bearer <jwt_token>`.

### Authentication Endpoints

#### POST /admin/auth/login
```json
Request:
{
  "email": "admin@example.com",
  "password": "SecurePass123!",
  "mfa_code": "123456"  // Optional, if MFA enabled
}

Response:
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "admin@example.com",
    "full_name": "Admin User",
    "role": "admin"
  }
}
```

#### POST /admin/auth/logout
```json
Response:
{
  "message": "Logged out successfully"
}
```

#### GET /admin/auth/me
```json
Response:
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "admin@example.com",
  "full_name": "Admin User",
  "role": "admin",
  "mfa_enabled": false
}
```

---

### API Key Management

#### POST /admin/api-keys
**Create new agent API key**

```json
Request:
{
  "name": "Production Agent",
  "description": "Main production CIRIS Agent instance",
  "environment": "live",
  "permissions": ["billing:read", "billing:write"],
  "expires_in_days": 90  // Optional
}

Response:
{
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "api_key": "cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r",  // ⚠️ Shown ONCE
  "key_prefix": "cbk_live_8f4d...",
  "name": "Production Agent",
  "description": "Main production CIRIS Agent instance",
  "environment": "live",
  "permissions": ["billing:read", "billing:write"],
  "created_at": "2025-01-08T10:00:00Z",
  "expires_at": "2025-04-08T10:00:00Z",
  "status": "active",
  "warning": "This API key will only be shown once. Please save it securely."
}
```

#### GET /admin/api-keys
**List all API keys**

```json
Response:
{
  "api_keys": [
    {
      "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "key_prefix": "cbk_live_8f4d...",
      "name": "Production Agent",
      "environment": "live",
      "permissions": ["billing:read", "billing:write"],
      "created_at": "2025-01-08T10:00:00Z",
      "expires_at": "2025-04-08T10:00:00Z",
      "last_used_at": "2025-01-08T15:30:00Z",
      "last_used_ip": "203.0.113.42",
      "status": "active"
    }
  ],
  "total": 1
}
```

#### DELETE /admin/api-keys/{key_id}
**Revoke API key**

```json
Response:
{
  "message": "API key revoked successfully",
  "revoked_at": "2025-01-08T16:00:00Z"
}
```

#### POST /admin/api-keys/{key_id}/rotate
**Rotate API key (generate new, deprecate old)**

```json
Response:
{
  "new_key": {
    "api_key": "cbk_live_9g5e0d3b2c8f6g4h7i9k1m3o5p7q9r2s",
    "key_prefix": "cbk_live_9g5e...",
    "expires_at": "2025-04-08T16:00:00Z"
  },
  "old_key": {
    "key_prefix": "cbk_live_8f4d...",
    "status": "rotating",
    "grace_period_until": "2025-01-09T16:00:00Z"
  },
  "warning": "Update your agent with the new key before grace period ends."
}
```

---

### User Analytics

#### GET /admin/users
**List all users with pagination and filtering**

```json
Query Params:
  ?page=1&limit=50&search=test-user&status=active&sort=total_uses:desc

Response:
{
  "users": [
    {
      "account_id": "550e8400-e29b-41d4-a716-446655440000",
      "oauth_provider": "oauth:google",
      "external_id": "test-user-1@example.com",
      "wa_id": "wa-test-001",
      "tenant_id": "tenant-acme",
      "plan_name": "pro",
      "status": "active",
      "balance_minor": 5000,
      "free_uses_remaining": 0,
      "total_uses": 25,
      "total_spent_minor": 5000,
      "created_at": "2024-12-09T10:00:00Z",
      "last_active_at": "2025-01-08T15:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 50,
    "total": 127,
    "total_pages": 3
  }
}
```

#### GET /admin/users/{account_id}
**Get detailed user information**

```json
Response:
{
  "account": {
    "account_id": "550e8400-e29b-41d4-a716-446655440000",
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "balance_minor": 5000,
    "free_uses_remaining": 0,
    "total_uses": 25,
    "status": "active",
    "created_at": "2024-12-09T10:00:00Z"
  },
  "usage_summary": {
    "total_charges": 20,
    "total_spent_minor": 2000,
    "total_credits": 2,
    "total_purchased_minor": 7000,
    "lifetime_value_minor": 7000
  },
  "recent_charges": [
    {
      "charge_id": "...",
      "amount_minor": 100,
      "description": "Agent interaction - datum",
      "created_at": "2025-01-08T15:30:00Z"
    }
  ],
  "recent_credits": [
    {
      "credit_id": "...",
      "amount_minor": 5000,
      "transaction_type": "purchase",
      "external_transaction_id": "pi_123...",
      "created_at": "2025-01-08T10:00:00Z"
    }
  ]
}
```

---

### Analytics & Reports

#### GET /admin/analytics/overview
**Dashboard overview stats**

```json
Response:
{
  "period": "today",
  "metrics": {
    "total_users": 1247,
    "active_users_today": 342,
    "new_users_today": 12,
    "revenue_today_minor": 145000,
    "revenue_yesterday_minor": 132000,
    "revenue_change_percent": 9.85,
    "total_charges_today": 2900,
    "avg_charge_minor": 50,
    "free_tier_users": 894,
    "paid_users": 353
  }
}
```

#### GET /admin/analytics/daily
**Daily aggregates**

```json
Query Params:
  ?start_date=2025-01-01&end_date=2025-01-08

Response:
{
  "daily_stats": [
    {
      "date": "2025-01-08",
      "unique_users": 342,
      "total_charges": 2900,
      "total_revenue_minor": 145000,
      "avg_charge_minor": 50,
      "new_users": 12,
      "purchases": 29,
      "purchase_revenue_minor": 14500
    },
    {
      "date": "2025-01-07",
      "unique_users": 318,
      "total_charges": 2640,
      "total_revenue_minor": 132000,
      "avg_charge_minor": 50,
      "new_users": 8,
      "purchases": 26,
      "purchase_revenue_minor": 13000
    }
  ]
}
```

#### GET /admin/analytics/weekly
**Weekly aggregates (last 12 weeks)**

```json
Response:
{
  "weekly_stats": [
    {
      "week_start": "2025-01-06",
      "week_end": "2025-01-12",
      "unique_users": 1847,
      "total_charges": 18234,
      "total_revenue_minor": 911700,
      "purchases": 182,
      "purchase_revenue_minor": 91000
    }
  ]
}
```

#### GET /admin/analytics/monthly
**Monthly aggregates**

```json
Response:
{
  "monthly_stats": [
    {
      "month": "2025-01",
      "unique_users": 3421,
      "total_charges": 52341,
      "total_revenue_minor": 2617050,
      "purchases": 523,
      "purchase_revenue_minor": 261500,
      "new_users": 234
    }
  ]
}
```

#### GET /admin/analytics/all-time
**All-time aggregates**

```json
Response:
{
  "all_time": {
    "total_users": 5247,
    "total_charges": 234521,
    "total_revenue_minor": 11726050,
    "total_purchases": 2345,
    "purchase_revenue_minor": 1172500,
    "avg_user_lifetime_value_minor": 2235,
    "first_transaction": "2024-11-15T08:00:00Z",
    "last_transaction": "2025-01-08T15:30:00Z"
  }
}
```

#### GET /admin/analytics/revenue-chart
**Revenue chart data (for visualization)**

```json
Query Params:
  ?granularity=daily&start_date=2025-01-01&end_date=2025-01-08

Response:
{
  "chart_data": [
    {"date": "2025-01-01", "revenue": 98500, "purchases": 19},
    {"date": "2025-01-02", "revenue": 102000, "purchases": 20},
    {"date": "2025-01-03", "revenue": 125000, "purchases": 25},
    {"date": "2025-01-04", "revenue": 115000, "purchases": 23},
    {"date": "2025-01-05", "revenue": 130000, "purchases": 26},
    {"date": "2025-01-06", "revenue": 142000, "purchases": 28},
    {"date": "2025-01-07", "revenue": 132000, "purchases": 26},
    {"date": "2025-01-08", "revenue": 145000, "purchases": 29}
  ]
}
```

---

### Configuration Management

#### GET /admin/config/providers
**List payment provider configurations**

```json
Response:
{
  "providers": [
    {
      "provider_type": "stripe",
      "is_active": true,
      "config": {
        "publishable_key": "pk_live_...",
        "webhook_endpoint": "https://billing.yourdomain.com/v1/billing/webhooks/stripe"
      },
      "updated_at": "2025-01-08T10:00:00Z"
    }
  ]
}
```

#### PUT /admin/config/providers/stripe
**Update Stripe configuration**

```json
Request:
{
  "api_key": "sk_live_...",
  "webhook_secret": "whsec_...",
  "publishable_key": "pk_live_..."
}

Response:
{
  "provider_type": "stripe",
  "is_active": true,
  "updated_at": "2025-01-08T16:00:00Z",
  "message": "Stripe configuration updated successfully"
}
```

#### GET /admin/config/billing
**Get billing system configuration**

```json
Response:
{
  "pricing": {
    "free_uses_per_account": 3,
    "paid_uses_per_purchase": 20,
    "price_per_purchase_minor": 500,
    "currency": "USD"
  },
  "database": {
    "primary_host": "postgresql-primary.vultr.internal",
    "replica_host": "postgresql-replica.vultr.internal",
    "connection_pool_size": 20,
    "max_overflow": 10
  },
  "rate_limits": {
    "credit_check_per_minute": 60,
    "charge_per_minute": 30,
    "purchase_per_hour": 10
  }
}
```

#### PUT /admin/config/billing
**Update billing configuration**

```json
Request:
{
  "pricing": {
    "free_uses_per_account": 5,
    "paid_uses_per_purchase": 25,
    "price_per_purchase_minor": 500
  }
}

Response:
{
  "message": "Billing configuration updated successfully",
  "updated_at": "2025-01-08T16:00:00Z",
  "restart_required": false
}
```

---

## UI Components

### Technology Stack

**Frontend:**
- **Framework**: React 18 with TypeScript
- **Styling**: Tailwind CSS + shadcn/ui components
- **Charts**: Recharts or Chart.js
- **Tables**: TanStack Table (React Table v8)
- **Forms**: React Hook Form + Zod validation
- **HTTP**: Axios or native fetch with React Query
- **Routing**: React Router v6
- **State**: Zustand or React Context

**Build:**
- Vite (fast dev server, optimized builds)
- TypeScript strict mode

### Page Structure

```
/admin/
  ├── /login                  # Login page
  ├── /dashboard              # Overview dashboard
  ├── /users                  # Users list & search
  ├── /users/:id              # User detail view
  ├── /api-keys               # API key management
  ├── /analytics              # Detailed analytics
  ├── /config                 # System configuration
  └── /audit-logs             # Admin action logs
```

---

### 1. Login Page (`/admin/login`)

```typescript
interface LoginFormData {
  email: string;
  password: string;
  mfaCode?: string;
}

const LoginPage = () => {
  const [mfaRequired, setMfaRequired] = useState(false);

  const onSubmit = async (data: LoginFormData) => {
    const response = await fetch('/admin/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });

    if (response.status === 403 && !data.mfaCode) {
      setMfaRequired(true);
      return;
    }

    const { access_token } = await response.json();
    localStorage.setItem('admin_token', access_token);
    navigate('/admin/dashboard');
  };

  return (
    <div className="login-container">
      <h1>CIRIS Billing Admin</h1>
      <form onSubmit={handleSubmit(onSubmit)}>
        <input type="email" name="email" placeholder="Email" />
        <input type="password" name="password" placeholder="Password" />
        {mfaRequired && (
          <input type="text" name="mfaCode" placeholder="2FA Code" />
        )}
        <button type="submit">Login</button>
      </form>
    </div>
  );
};
```

---

### 2. Dashboard Page (`/admin/dashboard`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Header: CIRIS Billing Admin | Logout                   │
├─────────────────────────────────────────────────────────┤
│  Sidebar Navigation                                      │
│  - Dashboard                                             │
│  - Users                                                 │
│  - API Keys                                              │
│  - Analytics                                             │
│  - Configuration                                         │
│  - Audit Logs                                            │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  OVERVIEW METRICS (Today)                                │
├───────────────┬───────────────┬───────────────┬─────────┤
│  Total Users  │ Active Today  │ Revenue Today │ Charges │
│  1,247        │  342 (+3.2%)  │  $1,450       │  2,900  │
└───────────────┴───────────────┴───────────────┴─────────┘

┌─────────────────────────────────────────────────────────┐
│  REVENUE CHART (Last 30 Days)                            │
│                                                           │
│  $2000 │              ╱╲                                  │
│        │         ╱╲  ╱  ╲        ╱╲                      │
│  $1500 │    ╱╲  ╱  ╲╱    ╲  ╱╲  ╱  ╲                     │
│        │   ╱  ╲╱          ╲╱  ╲╱    ╲                    │
│  $1000 │  ╱                          ╲                   │
│        └─────────────────────────────────────────        │
│          Jan 1    Jan 8    Jan 15   Jan 22   Jan 30      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  RECENT ACTIVITY                                         │
├─────────────────────────────────────────────────────────┤
│  • user@example.com purchased 20 uses ($5.00) - 2m ago   │
│  • test-user-1@example.com charged $0.50 - 5m ago        │
│  • Admin rotated API key "Production Agent" - 1h ago     │
└─────────────────────────────────────────────────────────┘
```

**Components:**
- `MetricCard` (total users, revenue, etc.)
- `RevenueChart` (line chart using Recharts)
- `ActivityFeed` (recent transactions)

---

### 3. Users Page (`/admin/users`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Users                                                   │
├─────────────────────────────────────────────────────────┤
│  [Search: ________] [Status: All ▼] [Plan: All ▼]       │
└─────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ User                    │ Plan │ Balance │ Uses │ Status │ Actions   │
├─────────────────────────┼──────┼─────────┼──────┼────────┼───────────┤
│ test-user-1@example.com │ Pro  │ $50.00  │  25  │ Active │ View Edit │
│ test-user-2@example.com │ Free │ $0.50   │  10  │ Active │ View Edit │
│ whale-user@example.com  │ Ent. │ $1000   │ 500  │ Active │ View Edit │
└─────────────────────────┴──────┴─────────┴──────┴────────┴───────────┘
                                             Page 1 of 25 [< 1 2 3 >]
```

**Features:**
- Real-time search (debounced)
- Filters: status, plan, balance range
- Sort: by uses, balance, created date
- Export to CSV
- Bulk actions (suspend, grant credits)

**Components:**
- `UsersTable` (TanStack Table)
- `UserSearchBar`
- `UserFilters`

---

### 4. User Detail Page (`/admin/users/:id`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  ← Back to Users                                         │
│  test-user-1@example.com                                 │
├─────────────────────────────────────────────────────────┤
│  Account Info          │  Usage Summary                  │
│  ID: 550e8400-...      │  Total Uses: 25                 │
│  Provider: Google      │  Total Spent: $20.00            │
│  Status: Active        │  Total Purchased: $70.00        │
│  Balance: $50.00       │  Lifetime Value: $70.00         │
│  Free Uses: 0/3        │  Avg per use: $0.80             │
│  Created: 2024-12-09   │  Last Active: 2m ago            │
└────────────────────────┴─────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  USAGE CHART (Last 30 Days)                              │
│  [Line chart showing daily usage]                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  RECENT CHARGES                                          │
├─────────────────────────────────────────────────────────┤
│  $0.50 - Agent interaction - 2m ago                      │
│  $1.00 - Agent interaction - 1h ago                      │
│  $0.50 - Agent interaction - 3h ago                      │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  PURCHASE HISTORY                                        │
├─────────────────────────────────────────────────────────┤
│  $50.00 - 20 uses - Stripe - 2 days ago                  │
│  $20.00 - 20 uses - Stripe - 30 days ago                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  ACTIONS                                                 │
│  [Grant Credits] [Suspend Account] [Change Plan]        │
└─────────────────────────────────────────────────────────┘
```

---

### 5. API Keys Page (`/admin/api-keys`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  API Keys                             [+ Create New Key] │
├─────────────────────────────────────────────────────────┤

┌──────────────────────────────────────────────────────────────────────┐
│ Name              │ Key Prefix       │ Env  │ Last Used │ Actions    │
├───────────────────┼──────────────────┼──────┼───────────┼────────────┤
│ Production Agent  │ cbk_live_8f4d... │ Live │ 2m ago    │ Rotate Del │
│ Staging Agent     │ cbk_test_9g5e... │ Test │ 1h ago    │ Rotate Del │
│ Dev Agent         │ cbk_test_2a3b... │ Test │ Never     │ Rotate Del │
└───────────────────┴──────────────────┴──────┴───────────┴────────────┘
```

**Create API Key Modal:**
```
┌─────────────────────────────────────────────────────────┐
│  Create New API Key                              [X]    │
├─────────────────────────────────────────────────────────┤
│  Name: [________________]                                │
│        e.g., "Production Agent"                          │
│                                                           │
│  Description: [____________________________]             │
│               e.g., "Main production instance"           │
│                                                           │
│  Environment: ○ Test  ● Live                             │
│                                                           │
│  Permissions:                                            │
│    ☑ billing:read                                        │
│    ☑ billing:write                                       │
│    ☐ billing:admin                                       │
│    ☐ analytics:read                                      │
│                                                           │
│  Expiration: [90 days ▼]                                 │
│                                                           │
│              [Cancel]  [Create Key]                      │
└─────────────────────────────────────────────────────────┘
```

**Success Modal (show key once):**
```
┌─────────────────────────────────────────────────────────┐
│  API Key Created Successfully                     [X]    │
├─────────────────────────────────────────────────────────┤
│  ⚠️  This key will only be shown once!                   │
│                                                           │
│  API Key:                                                │
│  ┌─────────────────────────────────────────────────┐    │
│  │ cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r  [📋] │    │
│  └─────────────────────────────────────────────────┘    │
│                                                           │
│  Please copy and save this key now.                      │
│  You won't be able to see it again.                      │
│                                                           │
│                                 [I've Saved It]          │
└─────────────────────────────────────────────────────────┘
```

---

### 6. Analytics Page (`/admin/analytics`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Analytics                                               │
│  Time Range: [Last 30 Days ▼]  [Custom Range...]        │
├─────────────────────────────────────────────────────────┤

┌───────────────────────────────────────────────────────────────┐
│  KEY METRICS                                                   │
├─────────────┬─────────────┬─────────────┬─────────────────────┤
│ Total       │ Active      │ Revenue     │ Purchases           │
│ Users       │ Users       │             │                     │
│ 1,247       │ 342         │ $14,500     │ 290 ($14,500)       │
│ ↑ 12.3%     │ ↑ 3.2%      │ ↑ 15.8%     │ ↑ 18.2%             │
└─────────────┴─────────────┴─────────────┴─────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  REVENUE OVER TIME                                       │
│  [Line chart with revenue + purchases over time]         │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  USER ACQUISITION                                        │
│  [Area chart showing new users over time]                │
└─────────────────────────────────────────────────────────┘

┌──────────────────────────┬──────────────────────────────┐
│  TOP USERS BY SPEND      │  PLAN DISTRIBUTION           │
│  1. whale-user  $1,000   │  [Pie chart]                 │
│  2. user-2      $500     │  Free: 72%                   │
│  3. user-3      $350     │  Pro: 20%                    │
│  ...                     │  Enterprise: 8%              │
└──────────────────────────┴──────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  EXPORT DATA                                             │
│  [Export CSV] [Export JSON] [Generate Report]           │
└─────────────────────────────────────────────────────────┘
```

---

### 7. Configuration Page (`/admin/config`)

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Configuration                                           │
├─────────────────────────────────────────────────────────┤

┌─────────────────────────────────────────────────────────┐
│  BILLING SETTINGS                                        │
├─────────────────────────────────────────────────────────┤
│  Free Uses Per Account: [3]                              │
│  Paid Uses Per Purchase: [20]                            │
│  Price Per Purchase: $[5.00]                             │
│  Currency: [USD ▼]                                       │
│                                                           │
│  [Save Changes]                                          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  STRIPE CONFIGURATION                                    │
├─────────────────────────────────────────────────────────┤
│  Status: ● Active                                        │
│                                                           │
│  Publishable Key: pk_live_************                   │
│  API Key: [********************] [Update]                │
│  Webhook Secret: [********************] [Update]         │
│  Webhook URL: https://billing.yourdomain.com/...         │
│                                                           │
│  [Test Connection] [Save Changes]                        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  DATABASE CONFIGURATION (Read-Only)                      │
├─────────────────────────────────────────────────────────┤
│  Primary: postgresql-primary.vultr.internal              │
│  Replica: postgresql-replica.vultr.internal              │
│  Pool Size: 20                                           │
│  Max Overflow: 10                                        │
│                                                           │
│  ℹ️  Database config managed via environment variables   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  RATE LIMITS                                             │
├─────────────────────────────────────────────────────────┤
│  Credit Checks: [60] per minute                          │
│  Charges: [30] per minute                                │
│  Purchases: [10] per hour                                │
│                                                           │
│  [Save Changes]                                          │
└─────────────────────────────────────────────────────────┘
```

---

## Security Considerations

### API Key Storage

1. **Never store plaintext keys**: Use Argon2id hashing
2. **Key prefix indexing**: Index only prefix for fast lookups
3. **Show key once**: After creation, never retrievable
4. **Secure transmission**: HTTPS only for admin UI
5. **Key rotation**: Grace period for seamless updates

### Admin Authentication

1. **Password requirements**: Min 12 chars, uppercase, lowercase, number, symbol
2. **MFA enforcement**: Required for super_admin role
3. **JWT short expiry**: 1 hour access tokens
4. **Secure cookie storage**: HttpOnly, Secure, SameSite=Strict
5. **Session invalidation**: Logout revokes JWT (add to blacklist)

### Audit Logging

Log all admin actions:
- API key creation/rotation/revocation
- Configuration changes
- User modifications (suspend, grant credits)
- Login attempts (success/failure)
- IP address + User-Agent tracking

### Rate Limiting

**Admin API:**
- 100 requests/minute per admin user
- 10 login attempts per IP per hour

**Agent API (with API key):**
- 60 credit checks per minute
- 30 charges per minute
- 10 purchases per hour

### RBAC (Role-Based Access Control)

```python
def require_role(required_role: str):
    def decorator(func):
        async def wrapper(current_user: AdminUser, *args, **kwargs):
            if current_user.role not in ROLE_HIERARCHY[required_role]:
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(current_user, *args, **kwargs)
        return wrapper
    return decorator

# Example usage
@router.post("/admin/api-keys")
@require_role("admin")
async def create_api_key(request: CreateAPIKeyRequest, current_user: AdminUser):
    ...
```

---

## Implementation Plan

### Phase 1: Database & Models (Week 1)

- [ ] Create Alembic migration for new tables
- [ ] Implement SQLAlchemy models (APIKey, AdminUser, ProviderConfig, AdminAuditLog)
- [ ] Write database seed script (create first super_admin)
- [ ] Test migrations on local database

### Phase 2: API Key Authentication (Week 1)

- [ ] Implement key generation algorithm
- [ ] Implement key validation middleware
- [ ] Add `X-API-Key` header requirement to billing endpoints
- [ ] Create admin endpoints for key CRUD
- [ ] Write unit tests for key validation

### Phase 3: Admin Authentication (Week 2)

- [ ] Implement admin login endpoint (POST /admin/auth/login)
- [ ] Implement JWT generation/validation
- [ ] Add admin authentication middleware
- [ ] Implement MFA (TOTP) support
- [ ] Create admin user CRUD endpoints

### Phase 4: Analytics & Reports (Week 2)

- [ ] Create materialized views for analytics
- [ ] Implement analytics endpoints (overview, daily, weekly, monthly)
- [ ] Add caching layer (Redis) for expensive queries
- [ ] Create CSV export functionality
- [ ] Write aggregation queries with optimizations

### Phase 5: Configuration Management (Week 3)

- [ ] Implement provider config endpoints
- [ ] Add config validation logic
- [ ] Create audit logging for config changes
- [ ] Implement secure secret storage (encrypt Stripe keys at rest)
- [ ] Add config change notifications (email admin on critical changes)

### Phase 6: Admin UI Development (Week 3-4)

- [ ] Set up React + Vite + TypeScript project
- [ ] Implement authentication (login, JWT storage)
- [ ] Build Dashboard page (metrics + charts)
- [ ] Build Users page (table + filters)
- [ ] Build User Detail page
- [ ] Build API Keys page (CRUD)
- [ ] Build Analytics page (charts + exports)
- [ ] Build Configuration page (forms)
- [ ] Add responsive design (mobile support)
- [ ] Write frontend unit tests

### Phase 7: Testing & Documentation (Week 5)

- [ ] Write API integration tests
- [ ] Write E2E tests for admin UI
- [ ] Performance testing (analytics queries)
- [ ] Security audit (OWASP top 10)
- [ ] Update DEPLOYMENT.md with admin UI deployment
- [ ] Create ADMIN_USER_GUIDE.md
- [ ] Record demo video

### Phase 8: Deployment (Week 5)

- [ ] Build Docker image for admin UI
- [ ] Add admin UI to docker-compose.yml
- [ ] Configure Nginx reverse proxy for admin UI
- [ ] Set up SSL for admin.billing.yourdomain.com
- [ ] Deploy to Vultr staging environment
- [ ] Run smoke tests
- [ ] Deploy to production

---

## Open Questions

1. **Admin UI hosting**: Same container as API or separate?
   - **Recommendation**: Separate subdomain (admin.billing.yourdomain.com)

2. **First admin user**: How to bootstrap?
   - **Recommendation**: CLI command `python -m app.cli create-admin --email admin@example.com`

3. **Analytics data retention**: How long to keep detailed logs?
   - **Recommendation**: 90 days detailed, aggregates forever

4. **Export formats**: CSV only or also JSON/PDF?
   - **Recommendation**: CSV + JSON (PDF overkill)

5. **Real-time updates**: WebSocket for live dashboard?
   - **Recommendation**: Polling every 30s initially, WebSocket in v2

---

## Success Criteria

✅ Admin can log in securely (password + MFA)
✅ Admin can create/rotate/revoke agent API keys
✅ Admin can view all users with search/filter
✅ Admin can view detailed user analytics
✅ Admin can view revenue aggregates (daily, weekly, monthly, all-time)
✅ Admin can configure Stripe credentials
✅ Admin can update billing pricing
✅ All admin actions are audit logged
✅ UI is responsive and works on mobile
✅ API responses are < 500ms (with caching)

---

## Next Steps

1. Review this architecture document
2. Approve database schema changes
3. Begin Phase 1 implementation
4. Set up CI/CD pipeline for admin UI
5. Schedule security review

