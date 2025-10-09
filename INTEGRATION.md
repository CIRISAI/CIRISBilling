# CIRIS Agent Integration Guide

Complete guide for integrating CIRIS Agent with the CIRIS Billing API, replacing Unlimit.com provider.

## Overview

This guide explains how to migrate from Unlimit.com to the self-hosted CIRIS Billing API. The new system provides:

- **3 free uses** per user (automatic)
- **Provider-agnostic architecture** (currently Stripe, easy to swap)
- **Self-hosted control** (no third-party dependency)
- **Type-safe API** (FastAPI with Pydantic validation)
- **Built-in observability** (metrics, logs, traces)

---

## Table of Contents

1. [API Overview](#api-overview)
2. [Agent API Endpoints for Frontend](#agent-api-endpoints-for-frontend)
3. [Migration from Unlimit](#migration-from-unlimit)
4. [Authentication](#authentication)
5. [Credit Check Flow](#credit-check-flow)
6. [Charge Creation Flow](#charge-creation-flow)
7. [Purchase Flow](#purchase-flow)
8. [Error Handling](#error-handling)
9. [Idempotency](#idempotency)
10. [Testing](#testing)
11. [Migration Checklist](#migration-checklist)

---

## API Overview

### Base URL

```
Production: https://billing.yourdomain.com
Staging:    https://billing-staging.yourdomain.com
Local:      http://localhost:8000
```

### Core Endpoints

| Endpoint | Method | Purpose | Agent Usage |
|----------|--------|---------|-------------|
| `/v1/billing/credits/check` | POST | Check if user has credit | **Before every interaction** |
| `/v1/billing/charges` | POST | Deduct credits | **After interaction completes** |
| `/v1/billing/accounts` | POST | Create/get account | Optional (auto-created) |
| `/v1/billing/purchases` | POST | Initiate payment | Frontend only |
| `/health` | GET | Health check | Monitoring |

### User Identity

All endpoints use the same identity structure:

```json
{
  "oauth_provider": "oauth:google",
  "external_id": "user@example.com",
  "wa_id": "wa-123",           // Optional: WebAgent ID
  "tenant_id": "tenant-acme"   // Optional: Multi-tenancy
}
```

**Identity Mapping:**
- `oauth_provider`: OAuth provider (e.g., `oauth:google`, `oauth:discord`, `oauth:github`)
- `external_id`: User's email or OAuth ID
- `wa_id`: WebAgent session ID (if applicable)
- `tenant_id`: Organization/tenant ID (for B2B)

---

## Agent API Endpoints for Frontend

**IMPORTANT**: The frontend should NEVER communicate directly with the billing backend. All billing-related requests must go through the agent API, which acts as a proxy.

### Architecture

```
Frontend (UI) → Agent API → Billing Backend
                    ↓
              (Authentication,
               Rate Limiting,
               Business Logic)
```

### Required Agent API Endpoints

The agent must expose these endpoints for the frontend:

#### 1. GET /api/credits

Get current user's credit balance and status.

**Request:**
```http
GET /api/credits HTTP/1.1
Host: agent.yourdomain.com
Authorization: Bearer <user_session_token>
```

**Response:**
```json
{
  "has_credit": true,
  "credits_remaining": 15,
  "free_uses_remaining": 0,
  "total_uses": 28,
  "plan_name": "pro",
  "purchase_required": false,
  "purchase_options": {
    "price_minor": 500,
    "uses": 20,
    "currency": "USD"
  }
}
```

**Implementation Example:**

```python
from fastapi import APIRouter, Depends
from typing import Dict, Any

router = APIRouter()

@router.get("/api/credits")
async def get_credits(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Get user's credit balance.

    Frontend calls this to display credit status.
    Agent proxies to billing backend.
    """
    from .billing_client import billing_client

    # Extract user identity
    user_identity = {
        "oauth_provider": user.oauth_provider,
        "external_id": user.external_id,
        "wa_id": user.wa_id,
        "tenant_id": user.tenant_id,
    }

    # Check credit via billing backend
    credit_check = await billing_client.check_credit(
        user=user_identity,
        context={
            "agent_id": "datum",
            "source": "frontend_credit_display"
        }
    )

    # Return frontend-friendly format
    return {
        "has_credit": credit_check["has_credit"],
        "credits_remaining": credit_check.get("credits_remaining", 0),
        "free_uses_remaining": credit_check.get("free_uses_remaining", 0),
        "total_uses": credit_check.get("total_uses", 0),
        "plan_name": credit_check.get("plan_name"),
        "purchase_required": credit_check.get("purchase_required", False),
        "purchase_options": {
            "price_minor": credit_check.get("purchase_price_minor"),
            "uses": credit_check.get("purchase_uses"),
            "currency": "USD"
        } if credit_check.get("purchase_required") else None
    }
```

#### 2. POST /api/purchase/initiate

Initiate a credit purchase (creates Stripe payment intent).

**Request:**
```http
POST /api/purchase/initiate HTTP/1.1
Host: agent.yourdomain.com
Authorization: Bearer <user_session_token>
Content-Type: application/json

{
  "return_url": "https://app.yourdomain.com/purchase/complete"
}
```

**Response:**
```json
{
  "payment_id": "pi_1234567890abcdef",
  "client_secret": "pi_1234567890abcdef_secret_xyz",
  "amount_minor": 500,
  "currency": "USD",
  "uses_purchased": 20,
  "publishable_key": "pk_live_..."
}
```

**Implementation Example:**

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

class PurchaseInitiateRequest(BaseModel):
    return_url: str | None = None

class PurchaseInitiateResponse(BaseModel):
    payment_id: str
    client_secret: str
    amount_minor: int
    currency: str
    uses_purchased: int
    publishable_key: str

@router.post("/api/purchase/initiate", response_model=PurchaseInitiateResponse)
async def initiate_purchase(
    request: PurchaseInitiateRequest,
    user: User = Depends(get_current_user)
) -> PurchaseInitiateResponse:
    """
    Initiate purchase flow.

    Frontend calls this when user clicks "Purchase Credits".
    Agent creates payment intent via billing backend.
    """
    from .billing_client import billing_client
    from app.config import settings

    try:
        # Create payment intent via billing backend
        purchase_response = await billing_client.create_purchase(
            oauth_provider=user.oauth_provider,
            external_id=user.external_id,
            wa_id=user.wa_id,
            tenant_id=user.tenant_id,
            customer_email=user.email,
            return_url=request.return_url
        )

        # Return with publishable key for Stripe.js
        return PurchaseInitiateResponse(
            payment_id=purchase_response["payment_id"],
            client_secret=purchase_response["client_secret"],
            amount_minor=purchase_response["amount_minor"],
            currency=purchase_response["currency"],
            uses_purchased=purchase_response["uses_purchased"],
            publishable_key=settings.stripe_publishable_key
        )

    except Exception as e:
        logger.error(f"Purchase initiation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to initiate purchase"
        )
```

#### 3. GET /api/purchase/status/{payment_id}

Check payment status (optional - for polling after payment).

**Request:**
```http
GET /api/purchase/status/pi_1234567890abcdef HTTP/1.1
Host: agent.yourdomain.com
Authorization: Bearer <user_session_token>
```

**Response:**
```json
{
  "status": "succeeded",
  "credits_added": 20,
  "balance_after": 20
}
```

**Implementation Example:**

```python
@router.get("/api/purchase/status/{payment_id}")
async def get_purchase_status(
    payment_id: str,
    user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Check if payment completed and credits were added.

    Frontend can poll this after payment to confirm credits.
    """
    from .billing_client import billing_client

    # Verify payment with billing backend (or Stripe directly)
    payment_status = await billing_client.check_payment_status(payment_id)

    if payment_status["succeeded"]:
        # Get updated credit balance
        credits = await get_credits(user)

        return {
            "status": "succeeded",
            "credits_added": payment_status.get("uses_added", 20),
            "balance_after": credits["credits_remaining"]
        }
    else:
        return {
            "status": payment_status["status"],
            "credits_added": 0,
            "balance_after": None
        }
```

### Agent Billing Client

Create a billing client module in your agent:

```python
# app/billing_client.py
"""Billing backend client for agent API."""

import httpx
from typing import Dict, Any, Optional
import os

BILLING_API_URL = os.getenv("BILLING_API_URL", "https://billing.yourdomain.com")

class BillingClient:
    """Client for communicating with billing backend."""

    def __init__(self, base_url: str = BILLING_API_URL):
        self.base_url = base_url
        self.timeout = 10.0

    async def check_credit(
        self,
        user: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Check user credit via billing backend."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/credits/check",
                json={
                    "oauth_provider": user["oauth_provider"],
                    "external_id": user["external_id"],
                    "wa_id": user.get("wa_id"),
                    "tenant_id": user.get("tenant_id"),
                    "context": context or {}
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

    async def create_purchase(
        self,
        oauth_provider: str,
        external_id: str,
        customer_email: str,
        wa_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        return_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Initiate purchase via billing backend."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/purchases",
                json={
                    "oauth_provider": oauth_provider,
                    "external_id": external_id,
                    "wa_id": wa_id,
                    "tenant_id": tenant_id,
                    "customer_email": customer_email,
                    "return_url": return_url
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

    async def create_charge(
        self,
        user: Dict[str, Any],
        amount_minor: int,
        description: str,
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create charge via billing backend."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/charges",
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
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

# Global instance
billing_client = BillingClient()
```

### Frontend Never Accesses Billing Backend

**IMPORTANT RULES:**

1. ❌ Frontend NEVER calls `billing.yourdomain.com` directly
2. ✅ Frontend ONLY calls agent API endpoints (`/api/credits`, `/api/purchase/initiate`)
3. ✅ Agent API validates user session
4. ✅ Agent API adds rate limiting
5. ✅ Agent API proxies to billing backend
6. ✅ Agent API translates responses for frontend

**Benefits of this architecture:**

- **Security**: Frontend never needs billing API credentials
- **Rate Limiting**: Agent can throttle requests per user
- **Business Logic**: Agent can add custom logic before/after billing calls
- **Abstraction**: Frontend doesn't need to know billing backend exists
- **Flexibility**: Can swap billing providers without frontend changes

---

## Migration from Unlimit

### Current Unlimit Flow

```python
# OLD: Unlimit.com integration
import unlimit

# Check credit
has_credit = unlimit.check_credit(user_email)

if has_credit:
    # Process agent interaction
    response = agent.process(message)

    # Deduct credit
    unlimit.charge(user_email, amount=100)
else:
    # Show payment prompt
    payment_url = unlimit.create_payment_link(user_email)
```

### New CIRIS Billing Flow

```python
# NEW: CIRIS Billing integration
import httpx

BILLING_API = "https://billing.yourdomain.com"

# Check credit (includes free tier)
credit_check = httpx.post(
    f"{BILLING_API}/v1/billing/credits/check",
    json={
        "oauth_provider": "oauth:google",
        "external_id": user_email,
        "context": {
            "agent_id": "datum",
            "channel_id": f"discord:{channel_id}",
            "request_id": request_id,
        }
    }
)

if credit_check.json()["has_credit"]:
    # Process agent interaction
    response = agent.process(message)

    # Deduct credit (free or paid)
    charge = httpx.post(
        f"{BILLING_API}/v1/billing/charges",
        json={
            "oauth_provider": "oauth:google",
            "external_id": user_email,
            "amount_minor": 100,  # 100 minor units (e.g., $0.01 or 1 use)
            "currency": "USD",
            "description": f"Agent interaction - {agent_id}",
            "idempotency_key": f"{request_id}-charge",
            "metadata": {
                "message_id": message_id,
                "agent_id": "datum",
                "channel_id": f"discord:{channel_id}",
                "request_id": request_id,
            }
        }
    )
else:
    # Show payment prompt (frontend handles payment)
    check_data = credit_check.json()
    if check_data.get("purchase_required"):
        # User exhausted free tier, needs to purchase
        return {
            "message": f"You've used your free tries. Purchase {check_data['purchase_uses']} uses for ${check_data['purchase_price_minor']/100:.2f}",
            "action": "purchase_required",
            "price": check_data["purchase_price_minor"],
            "uses": check_data["purchase_uses"]
        }
```

### Key Differences

| Feature | Unlimit | CIRIS Billing |
|---------|---------|---------------|
| **Free Tier** | ❌ No | ✅ 3 free uses per user |
| **Identity** | Email only | OAuth provider + ID |
| **Pricing** | Fixed by Unlimit | Self-configured ($5/20 uses) |
| **Idempotency** | ❌ No | ✅ Built-in |
| **Metadata** | Limited | Full context tracking |
| **Error Codes** | Proprietary | Standard HTTP codes |
| **Webhooks** | Unlimit webhooks | Stripe webhooks (configurable) |

---

## Authentication

### Option 1: No Authentication (Internal Network)

If your billing API is on a private network:

```python
import httpx

client = httpx.Client(base_url="https://billing.yourdomain.com")

# No auth headers needed
response = client.post("/v1/billing/credits/check", json={...})
```

### Option 2: API Key Authentication (Recommended)

If you add API key authentication later:

```python
import httpx
import os

client = httpx.Client(
    base_url="https://billing.yourdomain.com",
    headers={"X-API-Key": os.getenv("BILLING_API_KEY")}
)

response = client.post("/v1/billing/credits/check", json={...})
```

### Option 3: Mutual TLS (Enterprise)

For maximum security:

```python
import httpx
import ssl

context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
context.load_cert_chain(certfile="client.crt", keyfile="client.key")

client = httpx.Client(
    base_url="https://billing.yourdomain.com",
    verify=context
)
```

---

## Credit Check Flow

### When to Check Credit

**Check credit BEFORE starting agent interaction:**

```python
# ✅ CORRECT: Check first
credit_check = await check_credit(user)
if not credit_check["has_credit"]:
    return "Please purchase credits to continue"

# Process agent interaction
response = await agent.process(message)

# Charge after success
await create_charge(user, amount=100)
```

```python
# ❌ WRONG: Don't check after processing
response = await agent.process(message)  # Already incurred cost!

credit_check = await check_credit(user)  # Too late
if not credit_check["has_credit"]:
    return "Insufficient credits"  # User got free response
```

### Credit Check Request

```python
import httpx
from typing import Dict, Any

async def check_credit(
    oauth_provider: str,
    external_id: str,
    wa_id: str | None = None,
    tenant_id: str | None = None,
    context: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """
    Check if user has available credit (free or paid).

    Returns:
        {
            "has_credit": bool,
            "credits_remaining": int,
            "free_uses_remaining": int,
            "total_uses": int,
            "plan_name": str,
            "reason": str | None,
            "purchase_required": bool,
            "purchase_price_minor": int | None,
            "purchase_uses": int | None
        }
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://billing.yourdomain.com/v1/billing/credits/check",
            json={
                "oauth_provider": oauth_provider,
                "external_id": external_id,
                "wa_id": wa_id,
                "tenant_id": tenant_id,
                "context": context or {}
            },
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()


# Example usage
credit_check = await check_credit(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    context={
        "agent_id": "datum",
        "channel_id": "discord:123456789",
        "request_id": "req-abc-123"
    }
)

if credit_check["has_credit"]:
    # User has credit (free or paid)
    free_uses = credit_check.get("free_uses_remaining", 0)
    paid_credits = credit_check.get("credits_remaining", 0)

    if free_uses > 0:
        print(f"Using free tier: {free_uses} free uses remaining")
    else:
        print(f"Using paid credits: {paid_credits} credits remaining")

    # Proceed with agent interaction
    await process_agent_interaction()
else:
    # User has no credit
    reason = credit_check.get("reason")

    if credit_check.get("purchase_required"):
        # Show purchase prompt
        price = credit_check["purchase_price_minor"] / 100
        uses = credit_check["purchase_uses"]
        print(f"Purchase {uses} uses for ${price:.2f}")
    else:
        # Account issue (suspended, etc.)
        print(f"Cannot process: {reason}")
```

### Response Examples

**New User (3 Free Uses):**
```json
{
  "has_credit": true,
  "credits_remaining": 0,
  "free_uses_remaining": 3,
  "total_uses": 0,
  "plan_name": "free",
  "reason": null,
  "purchase_required": false,
  "purchase_price_minor": null,
  "purchase_uses": null
}
```

**Free Tier Exhausted:**
```json
{
  "has_credit": false,
  "credits_remaining": 0,
  "free_uses_remaining": 0,
  "total_uses": 3,
  "plan_name": "free",
  "reason": "No free uses or credits remaining",
  "purchase_required": true,
  "purchase_price_minor": 500,
  "purchase_uses": 20
}
```

**Paid User:**
```json
{
  "has_credit": true,
  "credits_remaining": 15,
  "free_uses_remaining": 0,
  "total_uses": 28,
  "plan_name": "pro",
  "reason": null,
  "purchase_required": false,
  "purchase_price_minor": null,
  "purchase_uses": null
}
```

**Suspended Account:**
```json
{
  "has_credit": false,
  "credits_remaining": 100,
  "free_uses_remaining": 0,
  "total_uses": 50,
  "plan_name": "pro",
  "reason": "Account suspended",
  "purchase_required": false,
  "purchase_price_minor": null,
  "purchase_uses": null
}
```

---

## Charge Creation Flow

### When to Create Charge

**Create charge AFTER agent interaction completes successfully:**

```python
try:
    # Check credit first
    credit_check = await check_credit(user)
    if not credit_check["has_credit"]:
        return {"error": "Insufficient credits"}

    # Process agent interaction
    agent_response = await agent.process(message)

    # Charge ONLY if interaction succeeded
    charge = await create_charge(
        user=user,
        amount_minor=100,
        description=f"Agent interaction - {agent_id}",
        metadata={
            "message_id": message.id,
            "agent_id": agent_id,
            "response_length": len(agent_response),
        }
    )

    return {
        "response": agent_response,
        "charge_id": charge["charge_id"],
        "balance_after": charge["balance_after"]
    }

except AgentError as e:
    # Don't charge if agent failed
    logger.error(f"Agent error: {e}")
    return {"error": "Agent failed, no charge"}
```

### Charge Request

```python
import httpx
from typing import Dict, Any
from uuid import uuid4

async def create_charge(
    oauth_provider: str,
    external_id: str,
    amount_minor: int,
    description: str,
    wa_id: str | None = None,
    tenant_id: str | None = None,
    idempotency_key: str | None = None,
    metadata: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """
    Create a charge (deduct credits from user).

    Args:
        oauth_provider: OAuth provider (e.g., "oauth:google")
        external_id: User identifier
        amount_minor: Amount in minor units (100 = 1 credit)
        description: Human-readable description
        idempotency_key: Prevents duplicate charges
        metadata: Context about the charge

    Returns:
        {
            "charge_id": str,
            "account_id": str,
            "amount_minor": int,
            "currency": str,
            "balance_after": int,
            "created_at": str,
            "description": str,
            "metadata": {...}
        }

    Raises:
        httpx.HTTPStatusError: On API errors
    """
    # Generate idempotency key if not provided
    if idempotency_key is None:
        idempotency_key = str(uuid4())

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://billing.yourdomain.com/v1/billing/charges",
            json={
                "oauth_provider": oauth_provider,
                "external_id": external_id,
                "wa_id": wa_id,
                "tenant_id": tenant_id,
                "amount_minor": amount_minor,
                "currency": "USD",
                "description": description,
                "idempotency_key": idempotency_key,
                "metadata": {
                    "message_id": metadata.get("message_id"),
                    "agent_id": metadata.get("agent_id"),
                    "channel_id": metadata.get("channel_id"),
                    "request_id": metadata.get("request_id"),
                } if metadata else {}
            },
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()


# Example usage
charge = await create_charge(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,  # 1 use
    description="Agent interaction - datum",
    idempotency_key=f"msg-{message_id}",  # Prevent duplicate charges
    metadata={
        "message_id": message_id,
        "agent_id": "datum",
        "channel_id": f"discord:{channel_id}",
        "request_id": request_id,
    }
)

print(f"Charged {charge['amount_minor']} credits")
print(f"Balance after: {charge['balance_after']}")
```

### Response Example

```json
{
  "charge_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "account_id": "550e8400-e29b-41d4-a716-446655440000",
  "amount_minor": 100,
  "currency": "USD",
  "balance_after": 1900,
  "created_at": "2025-01-08T12:34:56.789Z",
  "description": "Agent interaction - datum",
  "metadata": {
    "message_id": "msg-abc-123",
    "agent_id": "datum",
    "channel_id": "discord:123456789",
    "request_id": "req-xyz-789"
  }
}
```

---

## Purchase Flow

### Agent's Role

The **agent should NOT handle payments** directly. Instead:

1. Agent detects insufficient credits via credit check
2. Agent returns purchase prompt to frontend
3. Frontend handles payment UI (Stripe.js)
4. Webhook confirms payment → credits added automatically
5. Agent continues on next request

### Purchase Detection

```python
async def handle_message(user_id: str, message: str):
    """Handle incoming message from user."""

    # Check credit
    credit_check = await check_credit(
        oauth_provider=user.oauth_provider,
        external_id=user.external_id
    )

    if not credit_check["has_credit"]:
        if credit_check.get("purchase_required"):
            # Return purchase prompt to frontend
            return {
                "type": "purchase_required",
                "message": (
                    f"You've used your {credit_check['total_uses']} free tries! "
                    f"Purchase {credit_check['purchase_uses']} more uses "
                    f"for ${credit_check['purchase_price_minor']/100:.2f} to continue."
                ),
                "action": {
                    "type": "purchase",
                    "price_minor": credit_check["purchase_price_minor"],
                    "uses": credit_check["purchase_uses"],
                    "currency": "USD"
                }
            }
        else:
            # Account issue (suspended, etc.)
            return {
                "type": "error",
                "message": credit_check["reason"]
            }

    # Process message normally
    response = await agent.process(message)

    # Charge user
    await create_charge(
        oauth_provider=user.oauth_provider,
        external_id=user.external_id,
        amount_minor=100,
        description="Agent interaction",
        idempotency_key=f"msg-{message_id}"
    )

    return {
        "type": "response",
        "message": response
    }
```

### Frontend Purchase Flow

The frontend should handle the purchase UI:

```javascript
// Frontend JavaScript (React example)
async function handlePurchaseRequired(purchaseData) {
  // 1. Create payment intent
  const response = await fetch('/v1/billing/purchases', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      oauth_provider: user.oauthProvider,
      external_id: user.externalId,
      customer_email: user.email,
    })
  });

  const {client_secret, amount_minor, uses_purchased} = await response.json();

  // 2. Show Stripe payment form
  const stripe = Stripe('pk_live_your_publishable_key');
  const {error, paymentIntent} = await stripe.confirmCardPayment(
    client_secret,
    {
      payment_method: {
        card: cardElement,
        billing_details: {email: user.email}
      }
    }
  );

  if (error) {
    console.error('Payment failed:', error.message);
  } else if (paymentIntent.status === 'succeeded') {
    // 3. Credits added automatically via webhook
    alert(`Success! ${uses_purchased} uses added.`);

    // 4. Continue with agent interaction
    sendMessage(originalMessage);
  }
}
```

---

## Error Handling

### HTTP Status Codes

| Code | Meaning | Agent Action |
|------|---------|--------------|
| **200** | Success | Continue normally |
| **201** | Created | Resource created successfully |
| **400** | Bad Request | Log error, show user error message |
| **402** | Payment Required | Show purchase prompt |
| **403** | Forbidden | Account suspended, show message |
| **404** | Not Found | Account doesn't exist (should auto-create) |
| **409** | Conflict | Idempotency conflict, safe to ignore |
| **422** | Validation Error | Fix request data |
| **429** | Too Many Requests | Rate limited, retry with backoff |
| **500** | Server Error | Retry with exponential backoff |
| **503** | Service Unavailable | Retry with exponential backoff |

### Error Response Format

```json
{
  "detail": "Insufficient credits. Balance: 0, Required: 100"
}
```

### Handling Specific Errors

```python
import httpx
from typing import Dict, Any

async def create_charge_with_error_handling(
    user: Dict[str, str],
    amount_minor: int,
    description: str
) -> Dict[str, Any]:
    """Create charge with comprehensive error handling."""

    try:
        charge = await create_charge(
            oauth_provider=user["oauth_provider"],
            external_id=user["external_id"],
            amount_minor=amount_minor,
            description=description
        )
        return {"success": True, "charge": charge}

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        error_detail = e.response.json().get("detail", "Unknown error")

        if status_code == 402:
            # Insufficient credits
            return {
                "success": False,
                "error": "insufficient_credits",
                "message": "Please purchase more credits",
                "action": "purchase_required"
            }

        elif status_code == 403:
            # Account suspended or closed
            return {
                "success": False,
                "error": "account_suspended",
                "message": "Your account is suspended",
                "action": "contact_support"
            }

        elif status_code == 404:
            # Account not found (shouldn't happen with auto-create)
            return {
                "success": False,
                "error": "account_not_found",
                "message": "Account not found",
                "action": "retry"
            }

        elif status_code == 409:
            # Idempotency conflict (duplicate charge)
            # This is SAFE - charge already created
            logger.info(f"Duplicate charge for {description}")
            return {
                "success": True,
                "duplicate": True,
                "message": "Charge already recorded"
            }

        elif status_code == 422:
            # Validation error
            logger.error(f"Validation error: {error_detail}")
            return {
                "success": False,
                "error": "validation_error",
                "message": "Invalid request data"
            }

        elif status_code == 429:
            # Rate limited
            retry_after = int(e.response.headers.get("Retry-After", 60))
            return {
                "success": False,
                "error": "rate_limited",
                "message": "Too many requests",
                "retry_after": retry_after
            }

        elif status_code >= 500:
            # Server error - retry with backoff
            return {
                "success": False,
                "error": "server_error",
                "message": "Billing service unavailable",
                "action": "retry"
            }

        else:
            # Unknown error
            logger.error(f"Unexpected error: {status_code} - {error_detail}")
            return {
                "success": False,
                "error": "unknown_error",
                "message": "An error occurred"
            }

    except httpx.TimeoutException:
        # Request timeout
        return {
            "success": False,
            "error": "timeout",
            "message": "Billing service timeout",
            "action": "retry"
        }

    except httpx.NetworkError as e:
        # Network error
        logger.error(f"Network error: {e}")
        return {
            "success": False,
            "error": "network_error",
            "message": "Cannot reach billing service",
            "action": "retry"
        }
```

### Retry Strategy

```python
import asyncio
from typing import Callable, Any

async def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    **kwargs: Any
) -> Any:
    """
    Retry function with exponential backoff.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retries
        initial_delay: Initial delay in seconds
        backoff_factor: Multiply delay by this on each retry
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(**kwargs)

        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                # Don't retry client errors
                raise

            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s")
                await asyncio.sleep(delay)
                delay *= backoff_factor

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Network error, retrying in {delay}s")
                await asyncio.sleep(delay)
                delay *= backoff_factor

    # All retries exhausted
    raise last_exception


# Usage
charge = await retry_with_backoff(
    create_charge,
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,
    description="Agent interaction",
    max_retries=3
)
```

---

## Idempotency

### Why Idempotency Matters

Without idempotency, network errors can cause duplicate charges:

```
Agent → Billing API: Create charge
Billing API: ✓ Charge created, deducted credits
Billing API → Agent: [Network error, response lost]
Agent: "No response, retry..."
Agent → Billing API: Create charge (DUPLICATE!)
Billing API: ✓ Charge created AGAIN (user double-charged!)
```

**With idempotency:**

```
Agent → Billing API: Create charge (key: msg-123)
Billing API: ✓ Charge created, deducted credits
Billing API → Agent: [Network error, response lost]
Agent: "No response, retry..."
Agent → Billing API: Create charge (key: msg-123, SAME KEY)
Billing API: ✗ 409 Conflict (charge already exists, no double charge)
Agent: "409 = already charged, safe to continue"
```

### Using Idempotency Keys

```python
# ✅ CORRECT: Use unique, deterministic key
charge = await create_charge(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,
    description="Agent interaction",
    idempotency_key=f"msg-{message_id}",  # Unique per message
    metadata={"message_id": message_id}
)

# ✅ CORRECT: Use request ID
charge = await create_charge(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,
    description="Agent interaction",
    idempotency_key=f"{request_id}-charge",  # Unique per request
    metadata={"request_id": request_id}
)

# ❌ WRONG: Random key (defeats idempotency)
charge = await create_charge(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,
    description="Agent interaction",
    idempotency_key=str(uuid4()),  # Different every time!
    metadata={}
)

# ❌ WRONG: No key (allows duplicates)
charge = await create_charge(
    oauth_provider="oauth:google",
    external_id="user@example.com",
    amount_minor=100,
    description="Agent interaction",
    # No idempotency_key!
    metadata={}
)
```

### Handling Idempotency Conflicts

```python
try:
    charge = await create_charge(
        oauth_provider="oauth:google",
        external_id="user@example.com",
        amount_minor=100,
        description="Agent interaction",
        idempotency_key=f"msg-{message_id}"
    )
    print(f"Charge created: {charge['charge_id']}")

except httpx.HTTPStatusError as e:
    if e.response.status_code == 409:
        # Idempotency conflict - charge already exists
        # This is SAFE - user was only charged once
        existing_charge_id = e.response.headers.get("X-Existing-Charge-ID")
        logger.info(f"Charge already exists: {existing_charge_id}")

        # Continue normally - charge was already recorded
        print("Charge already recorded (idempotency)")
    else:
        raise
```

---

## Testing

### Local Testing

```python
# 1. Start local stack
# make test-local

# 2. Test credit check
import httpx

client = httpx.Client(base_url="http://localhost:8000")

# Check credit for new user
response = client.post(
    "/v1/billing/credits/check",
    json={
        "oauth_provider": "oauth:google",
        "external_id": "test-user@example.com",
        "context": {}
    }
)
print(response.json())
# Should show: free_uses_remaining: 3

# 3. Create charge (use free tier)
response = client.post(
    "/v1/billing/charges",
    json={
        "oauth_provider": "oauth:google",
        "external_id": "test-user@example.com",
        "amount_minor": 100,
        "currency": "USD",
        "description": "Test charge",
        "idempotency_key": "test-charge-1",
        "metadata": {}
    }
)
print(response.json())
# Should succeed, deduct free use

# 4. Check credit again
response = client.post(
    "/v1/billing/credits/check",
    json={
        "oauth_provider": "oauth:google",
        "external_id": "test-user@example.com",
        "context": {}
    }
)
print(response.json())
# Should show: free_uses_remaining: 2
```

### Integration Tests

```python
import pytest
import httpx
from ciris_agent import Agent

BILLING_API = "http://localhost:8000"

@pytest.fixture
def test_user():
    """Generate unique test user."""
    import uuid
    return {
        "oauth_provider": "oauth:google",
        "external_id": f"test-{uuid.uuid4()}@example.com"
    }

@pytest.mark.asyncio
async def test_free_tier_flow(test_user):
    """Test complete free tier flow."""
    async with httpx.AsyncClient(base_url=BILLING_API) as client:
        # 1. Check credit (new user, should have 3 free uses)
        response = await client.post(
            "/v1/billing/credits/check",
            json={**test_user, "context": {}}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_credit"] is True
        assert data["free_uses_remaining"] == 3

        # 2. Use first free use
        response = await client.post(
            "/v1/billing/charges",
            json={
                **test_user,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge 1",
                "idempotency_key": "test-1",
                "metadata": {}
            }
        )
        assert response.status_code == 201

        # 3. Check credit again (should have 2 free uses)
        response = await client.post(
            "/v1/billing/credits/check",
            json={**test_user, "context": {}}
        )
        data = response.json()
        assert data["free_uses_remaining"] == 2

@pytest.mark.asyncio
async def test_idempotency(test_user):
    """Test idempotency prevents duplicate charges."""
    async with httpx.AsyncClient(base_url=BILLING_API) as client:
        idempotency_key = "idempotency-test-1"

        # 1. Create charge
        response1 = await client.post(
            "/v1/billing/charges",
            json={
                **test_user,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Idempotency test",
                "idempotency_key": idempotency_key,
                "metadata": {}
            }
        )
        assert response1.status_code == 201
        charge_id_1 = response1.json()["charge_id"]

        # 2. Retry with same idempotency key
        response2 = await client.post(
            "/v1/billing/charges",
            json={
                **test_user,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Idempotency test",
                "idempotency_key": idempotency_key,  # SAME KEY
                "metadata": {}
            }
        )
        assert response2.status_code == 409  # Conflict

        # 3. Verify user was only charged once
        # (Check balance didn't decrease twice)

@pytest.mark.asyncio
async def test_insufficient_credits(test_user):
    """Test insufficient credits error."""
    async with httpx.AsyncClient(base_url=BILLING_API) as client:
        # Use all 3 free uses
        for i in range(3):
            await client.post(
                "/v1/billing/charges",
                json={
                    **test_user,
                    "amount_minor": 100,
                    "currency": "USD",
                    "description": f"Test charge {i+1}",
                    "idempotency_key": f"test-{i+1}",
                    "metadata": {}
                }
            )

        # 4th charge should fail (no paid credits)
        response = await client.post(
            "/v1/billing/charges",
            json={
                **test_user,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge 4",
                "idempotency_key": "test-4",
                "metadata": {}
            }
        )
        assert response.status_code == 402  # Payment Required
        assert "insufficient" in response.json()["detail"].lower()
```

### Load Testing

```python
# locustfile.py - Load testing with Locust
from locust import HttpUser, task, between

class BillingAPIUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Initialize user."""
        import uuid
        self.user_id = f"load-test-{uuid.uuid4()}@example.com"
        self.request_count = 0

    @task(10)  # 10x more credit checks than charges
    def check_credit(self):
        """Check credit."""
        self.client.post(
            "/v1/billing/credits/check",
            json={
                "oauth_provider": "oauth:google",
                "external_id": self.user_id,
                "context": {
                    "agent_id": "datum",
                    "request_id": f"req-{self.request_count}"
                }
            },
            name="/v1/billing/credits/check"
        )

    @task(1)
    def create_charge(self):
        """Create charge."""
        self.request_count += 1
        self.client.post(
            "/v1/billing/charges",
            json={
                "oauth_provider": "oauth:google",
                "external_id": self.user_id,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Load test charge",
                "idempotency_key": f"load-{self.request_count}",
                "metadata": {
                    "request_id": f"req-{self.request_count}"
                }
            },
            name="/v1/billing/charges"
        )

# Run: locust -f locustfile.py --host http://localhost:8000
```

---

## Migration Checklist

### Pre-Migration

- [ ] Review CIRIS Billing API documentation
- [ ] Set up local testing environment (`make test-local`)
- [ ] Test credit check flow locally
- [ ] Test charge creation flow locally
- [ ] Test error handling (insufficient credits, etc.)
- [ ] Set up staging billing API
- [ ] Configure Stripe test keys in staging
- [ ] Test purchase flow end-to-end in staging

### Code Changes

- [ ] Add billing API client to agent codebase
- [ ] Replace Unlimit credit check with CIRIS Billing credit check
- [ ] Replace Unlimit charge with CIRIS Billing charge
- [ ] Add idempotency key generation
- [ ] Add error handling for new status codes
- [ ] Add retry logic with exponential backoff
- [ ] Update user identity mapping (email → OAuth provider)
- [ ] Add metadata collection (message ID, agent ID, etc.)
- [ ] Update purchase flow UI (Stripe.js integration)
- [ ] Add free tier messaging ("X free uses remaining")

### Testing

- [ ] Test with new user (3 free uses)
- [ ] Test free tier depletion
- [ ] Test purchase flow
- [ ] Test paid credits usage
- [ ] Test idempotency (retry same request)
- [ ] Test error scenarios (suspended account, etc.)
- [ ] Load test against staging (100+ concurrent users)
- [ ] Test failover (stop one billing API instance)
- [ ] Test database connection failure handling
- [ ] Test webhook delivery (Stripe → billing API)

### Deployment

- [ ] Deploy billing API to production (see DEPLOYMENT.md)
- [ ] Configure production Stripe keys
- [ ] Set up Cloudflare DNS and SSL
- [ ] Configure Stripe webhook endpoint
- [ ] Deploy agent with new billing integration
- [ ] Enable feature flag for CIRIS Billing
- [ ] Monitor error rates and latency
- [ ] Monitor Stripe webhook delivery
- [ ] Set up alerts for billing API errors

### Monitoring

- [ ] Add billing API metrics to Grafana
- [ ] Set up alerts for high error rates
- [ ] Monitor free tier usage patterns
- [ ] Monitor purchase conversion rate
- [ ] Track average revenue per user
- [ ] Monitor API latency (p50, p95, p99)
- [ ] Set up PagerDuty/alerts for billing API downtime

### Rollback Plan

- [ ] Document Unlimit integration (in case of rollback)
- [ ] Keep Unlimit integration code (comment out)
- [ ] Have feature flag to switch back to Unlimit
- [ ] Test rollback procedure in staging
- [ ] Document rollback steps

### Post-Migration

- [ ] Verify all users migrated successfully
- [ ] Check for duplicate charges (idempotency working)
- [ ] Monitor free tier → paid conversion rate
- [ ] Gather user feedback on purchase flow
- [ ] Optimize based on metrics
- [ ] Remove Unlimit integration code
- [ ] Update documentation
- [ ] Celebrate! 🎉

---

## Example: Complete Agent Integration

```python
"""
CIRIS Agent - Billing Integration
Replaces Unlimit.com with CIRIS Billing API
"""

import httpx
import logging
from typing import Dict, Any, Optional
from uuid import uuid4
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BILLING_API_URL = "https://billing.yourdomain.com"


@dataclass
class UserIdentity:
    """User identity for billing."""
    oauth_provider: str
    external_id: str
    wa_id: Optional[str] = None
    tenant_id: Optional[str] = None


class BillingClient:
    """Client for CIRIS Billing API."""

    def __init__(self, base_url: str = BILLING_API_URL, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout

    async def check_credit(
        self,
        user: UserIdentity,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Check if user has available credit."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/credits/check",
                json={
                    "oauth_provider": user.oauth_provider,
                    "external_id": user.external_id,
                    "wa_id": user.wa_id,
                    "tenant_id": user.tenant_id,
                    "context": context or {}
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()

    async def create_charge(
        self,
        user: UserIdentity,
        amount_minor: int,
        description: str,
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create a charge (deduct credits)."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/charges",
                json={
                    "oauth_provider": user.oauth_provider,
                    "external_id": user.external_id,
                    "wa_id": user.wa_id,
                    "tenant_id": user.tenant_id,
                    "amount_minor": amount_minor,
                    "currency": "USD",
                    "description": description,
                    "idempotency_key": idempotency_key,
                    "metadata": {
                        "message_id": metadata.get("message_id"),
                        "agent_id": metadata.get("agent_id"),
                        "channel_id": metadata.get("channel_id"),
                        "request_id": metadata.get("request_id"),
                    } if metadata else {}
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()


class CIRISAgent:
    """CIRIS Agent with billing integration."""

    def __init__(self, agent_id: str = "datum"):
        self.agent_id = agent_id
        self.billing = BillingClient()

    async def process_message(
        self,
        user: UserIdentity,
        message: str,
        message_id: str,
        channel_id: str
    ) -> Dict[str, Any]:
        """
        Process user message with billing.

        Returns:
            {
                "type": "response" | "purchase_required" | "error",
                "message": str,
                "data": {...}
            }
        """
        request_id = str(uuid4())

        try:
            # 1. Check credit BEFORE processing
            credit_check = await self.billing.check_credit(
                user=user,
                context={
                    "agent_id": self.agent_id,
                    "channel_id": channel_id,
                    "request_id": request_id,
                }
            )

            if not credit_check["has_credit"]:
                # No credit - check if purchase required
                if credit_check.get("purchase_required"):
                    return {
                        "type": "purchase_required",
                        "message": (
                            f"You've used your {credit_check['total_uses']} free tries! "
                            f"Purchase {credit_check['purchase_uses']} more uses "
                            f"for ${credit_check['purchase_price_minor']/100:.2f} to continue."
                        ),
                        "data": {
                            "price_minor": credit_check["purchase_price_minor"],
                            "uses": credit_check["purchase_uses"],
                            "currency": "USD"
                        }
                    }
                else:
                    # Account issue
                    return {
                        "type": "error",
                        "message": credit_check["reason"],
                        "data": {}
                    }

            # 2. Process agent interaction
            logger.info(f"Processing message: {message_id}")
            agent_response = await self._run_agent(message)

            # 3. Create charge AFTER successful processing
            try:
                charge = await self.billing.create_charge(
                    user=user,
                    amount_minor=100,  # 1 use
                    description=f"Agent interaction - {self.agent_id}",
                    idempotency_key=f"msg-{message_id}",
                    metadata={
                        "message_id": message_id,
                        "agent_id": self.agent_id,
                        "channel_id": channel_id,
                        "request_id": request_id,
                    }
                )

                logger.info(
                    f"Charge created: {charge['charge_id']} "
                    f"(balance after: {charge['balance_after']})"
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    # Idempotency conflict - charge already exists
                    logger.info("Charge already recorded (idempotency)")
                else:
                    # Other error - log but don't fail response
                    logger.error(f"Charge failed: {e}")

            # 4. Return agent response
            return {
                "type": "response",
                "message": agent_response,
                "data": {
                    "charge_id": charge.get("charge_id"),
                    "balance_after": charge.get("balance_after"),
                    "free_uses_remaining": credit_check.get("free_uses_remaining")
                }
            }

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            return {
                "type": "error",
                "message": "An error occurred processing your message",
                "data": {}
            }

    async def _run_agent(self, message: str) -> str:
        """Run the actual agent logic."""
        # Your agent implementation here
        return f"Agent response to: {message}"


# Example usage
async def main():
    agent = CIRISAgent(agent_id="datum")

    user = UserIdentity(
        oauth_provider="oauth:google",
        external_id="user@example.com"
    )

    result = await agent.process_message(
        user=user,
        message="Hello, agent!",
        message_id="msg-123",
        channel_id="discord:123456789"
    )

    print(f"Type: {result['type']}")
    print(f"Message: {result['message']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## Summary

### Key Points

1. **Check credit BEFORE processing** - Don't waste resources on users without credit
2. **Always use idempotency keys** - Prevent duplicate charges
3. **Handle errors gracefully** - Network errors, rate limits, etc.
4. **Let frontend handle payments** - Agent only detects when purchase is needed
5. **Use retry logic** - For network/server errors only
6. **Track metadata** - Message ID, agent ID, channel ID for debugging
7. **Monitor metrics** - Credit checks, charges, errors, latency

### Migration Timeline

- **Week 1**: Set up local testing, code integration
- **Week 2**: Deploy to staging, test end-to-end
- **Week 3**: Load testing, optimize
- **Week 4**: Deploy to production, monitor

### Support

- **API Documentation**: https://billing.yourdomain.com/docs
- **Local Testing**: See LOCAL_TESTING.md
- **Deployment**: See DEPLOYMENT.md
- **Stripe Integration**: See STRIPE_INTEGRATION.md

---

🎉 **You're ready to migrate from Unlimit to CIRIS Billing!**

Next steps:
1. Review this integration guide
2. Set up local testing environment
3. Test credit check and charge flows
4. Deploy to staging and test end-to-end
5. Monitor metrics and optimize
6. Deploy to production
