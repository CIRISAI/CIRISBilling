# API Key Authentication - Quick Start Guide

**Version:** 1.0
**Date:** 2025-10-08
**Status:** Ready for Testing

---

## Overview

CIRIS Billing API now requires **API key authentication** for all agent requests. This prevents unauthorized access while maintaining simplicity.

### What Changed

**Before:**
```bash
curl -X POST https://billing.yourdomain.com/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{"oauth_provider": "oauth:google", "external_id": "user@example.com"}'
```

**After (with API key):**
```bash
curl -X POST https://billing.yourdomain.com/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -H "X-API-Key: cbk_live_abc123..." \
  -d '{"oauth_provider": "oauth:google", "external_id": "user@example.com"}'
```

---

## Quick Setup (5 Steps)

### Step 1: Run Database Migration

```bash
cd /home/emoore/CIRISBilling

# Run migration to create admin tables
docker-compose exec api alembic upgrade head

# Or locally
alembic upgrade head
```

### Step 2: Create First Admin User

Create a Python script to bootstrap the first admin:

```python
# scripts/create_admin.py
import asyncio
from argon2 import PasswordHasher
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://ciris:password@localhost:5432/ciris_billing"

async def create_admin():
    from app.db.models import AdminUser, Base

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    ph = PasswordHasher()
    password_hash = ph.hash("YourSecurePassword123!")

    admin = AdminUser(
        id=uuid4(),
        email="admin@yourdomain.com",
        password_hash=password_hash,
        full_name="System Administrator",
        role="super_admin",
        is_active=True,
        mfa_enabled=False
    )

    session.add(admin)
    session.commit()

    print(f"✅ Admin user created: {admin.email}")
    print(f"   User ID: {admin.id}")
    print(f"   Role: {admin.role}")

if __name__ == "__main__":
    asyncio.run(create_admin())
```

Run it:
```bash
python scripts/create_admin.py
```

### Step 3: Generate API Key

Create a script to generate an API key:

```python
# scripts/generate_api_key.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from uuid import UUID

DATABASE_URL = "postgresql+asyncpg://ciris:password@localhost:5432/ciris_billing"

async def generate_key():
    from app.services.api_key import APIKeyService

    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        api_key_service = APIKeyService(session)

        # Replace with your admin user ID from Step 2
        admin_user_id = UUID("YOUR-ADMIN-USER-ID-HERE")

        generated_key = await api_key_service.create_api_key(
            name="Production Agent",
            created_by=admin_user_id,
            environment="live",
            description="Main production CIRIS Agent instance",
            permissions=["billing:read", "billing:write"],
            expires_in_days=None  # Never expires
        )

        print("=" * 70)
        print("⚠️  API KEY GENERATED - SAVE THIS NOW!")
        print("=" * 70)
        print(f"Key ID:      {generated_key.key_id}")
        print(f"Name:        {generated_key.name}")
        print(f"Environment: {generated_key.environment}")
        print(f"Permissions: {', '.join(generated_key.permissions)}")
        print(f"\nAPI Key (SAVE THIS - shown only once):")
        print(f"  {generated_key.plaintext_key}")
        print("=" * 70)
        print("\n⚠️  Store this key securely. You will not be able to see it again!")

if __name__ == "__main__":
    asyncio.run(generate_key())
```

Run it:
```bash
python scripts/generate_api_key.py
```

**Output:**
```
======================================================================
⚠️  API KEY GENERATED - SAVE THIS NOW!
======================================================================
Key ID:      7c9e6679-7425-40de-944b-e07fc1f90ae7
Name:        Production Agent
Environment: live
Permissions: billing:read, billing:write

API Key (SAVE THIS - shown only once):
  cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r8s9t0u1v2w3x4y5z6
======================================================================

⚠️  Store this key securely. You will not be able to see it again!
```

### Step 4: Update CIRISAgent Configuration

Add the API key to your agent's environment:

```bash
# .env
BILLING_API_KEY=cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r8s9t0u1v2w3x4y5z6
```

Update your agent's billing client:

```python
# In CIRISAgent/app/services/billing_client.py

import httpx
import os

class BillingClient:
    def __init__(self):
        self.base_url = "https://billing.yourdomain.com"
        self.api_key = os.getenv("BILLING_API_KEY")
        if not self.api_key:
            raise ValueError("BILLING_API_KEY environment variable not set")

    async def check_credit(self, user, context=None):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/credits/check",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key  # ← Add this header
                },
                json={
                    "oauth_provider": user["oauth_provider"],
                    "external_id": user["external_id"],
                    "wa_id": user.get("wa_id"),
                    "tenant_id": user.get("tenant_id"),
                    "context": context or {}
                }
            )
            response.raise_for_status()
            return response.json()

    async def create_charge(self, user, amount_minor, description, idempotency_key, metadata=None):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/charges",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key  # ← Add this header
                },
                json={
                    "oauth_provider": user["oauth_provider"],
                    "external_id": user["external_id"],
                    "wa_id": user.get("wa_id"),
                    "tenant_id": user.get("tenant_id"),
                    "amount_minor": amount_minor,
                    "currency": "USD",
                    "description": description,
                    "idempotency_key": idempotency_key,
                    "metadata": metadata or {}
                }
            )
            response.raise_for_status()
            return response.json()
```

### Step 5: Test the Integration

```bash
# Test credit check with API key
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -H "X-API-Key: cbk_live_8f4d9c2a1b7e5f3g6h8j9k0m2n4p5q7r8s9t0u1v2w3x4y5z6" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "wa_id": "wa-test-001",
    "tenant_id": "tenant-acme"
  }'
```

**Expected response:**
```json
{
  "has_credit": true,
  "credits_remaining": 5000,
  "free_uses_remaining": 0,
  "total_uses": 25,
  "plan_name": "pro",
  "purchase_required": false
}
```

**Test without API key (should fail):**
```bash
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com"
  }'
```

**Expected error:**
```json
{
  "detail": "Missing required header X-API-Key"
}
```

---

## API Key Management

### List All API Keys

```python
from app.services.api_key import APIKeyService

async with async_session() as session:
    api_key_service = APIKeyService(session)
    keys = await api_key_service.list_api_keys()

    for key in keys:
        print(f"{key.name}: {key.key_prefix}... ({key.status})")
```

### Revoke API Key

```python
from uuid import UUID

async with async_session() as session:
    api_key_service = APIKeyService(session)
    await api_key_service.revoke_api_key(
        key_id=UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    )
    print("✅ API key revoked")
```

### Rotate API Key

```python
async with async_session() as session:
    api_key_service = APIKeyService(session)
    new_key = await api_key_service.rotate_api_key(
        key_id=UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7"),
        grace_period_hours=24  # Old key valid for 24 more hours
    )

    print(f"New API key: {new_key.plaintext_key}")
    print("Old key will be revoked after grace period")
```

---

## Permissions Model

Each API key has a list of permissions:

| Permission | Grants Access To |
|------------|------------------|
| `billing:read` | GET /v1/billing/accounts, POST /v1/billing/credits/check |
| `billing:write` | POST /v1/billing/charges, POST /v1/billing/credits, POST /v1/billing/purchases |
| `billing:admin` | Account suspension, manual balance adjustments |
| `analytics:read` | Usage statistics, revenue reports |

**Default agent key:** `["billing:read", "billing:write"]`

---

## Error Handling

### 401 Unauthorized

**Cause:** Missing, invalid, or expired API key

**Response:**
```json
{
  "detail": "Authentication failed: Invalid API key"
}
```

**Fix:** Check X-API-Key header is present and valid

### 403 Forbidden

**Cause:** API key lacks required permission

**Response:**
```json
{
  "detail": "Missing required permission: billing:write"
}
```

**Fix:** Generate new key with correct permissions or grant permission to existing key

---

## Security Best Practices

1. **Never commit API keys to git**
   - Use environment variables
   - Add `.env` to `.gitignore`

2. **Use test keys in development**
   - Generate `cbk_test_...` keys for local testing
   - Use `cbk_live_...` only in production

3. **Rotate keys regularly**
   - Rotate every 90 days
   - Use 24-hour grace period for zero-downtime rotation

4. **Monitor key usage**
   - Check `last_used_at` timestamp
   - Revoke unused keys

5. **Limit permissions**
   - Only grant permissions the agent needs
   - Use read-only keys where possible

---

## Troubleshooting

### Issue: "Missing required header X-API-Key"

**Cause:** Header not sent or misspelled

**Fix:**
```python
headers = {
    "X-API-Key": api_key  # Correct
    # NOT "API-Key" or "X-Api-Key"
}
```

### Issue: "Authentication failed: Invalid API key"

**Cause:** Key not found in database or hash mismatch

**Fix:**
1. Verify key is copied correctly (no extra spaces)
2. Check key status: `SELECT status FROM api_keys WHERE key_prefix = 'cbk_live_8f4d...'`
3. Regenerate key if lost

### Issue: "API key expired"

**Cause:** Key past `expires_at` timestamp

**Fix:** Generate new key with extended expiration

---

## Next Steps

1. ✅ **API key authentication implemented**
2. ⏭️ **Build Admin UI** to manage keys via web interface (see ADMIN_UI.md)
3. ⏭️ **Add rate limiting** per API key
4. ⏭️ **Set up monitoring** for failed auth attempts
5. ⏭️ **Implement key rotation automation** (90-day expiry)

---

## Migration Checklist

- [ ] Run database migration (`alembic upgrade head`)
- [ ] Create first admin user
- [ ] Generate production API key
- [ ] Update CIRISAgent with `BILLING_API_KEY` env var
- [ ] Update agent billing client to send `X-API-Key` header
- [ ] Test with local billing API
- [ ] Deploy to staging
- [ ] Test on staging
- [ ] Generate production key for live environment
- [ ] Deploy to production
- [ ] Monitor for auth failures

---

## Support

- See `ADMIN_UI.md` for full admin system architecture
- See `INTEGRATION.md` for agent integration details
- See `DEPLOYMENT.md` for production deployment

