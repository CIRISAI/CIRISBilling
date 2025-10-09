# CIRIS Billing API - Agent Integration Guide

## Overview

The CIRIS Billing API provides credit-based usage tracking and payment processing for AI agents. This guide covers integration, authentication, and API usage.

**Base URL:** `https://billing.ciris.ai`

✅ **HTTPS is fully operational** - All API requests are secured with TLS

---

## Authentication

All billing API endpoints require authentication using an API key.

### API Key Format

API keys use the format: `cbk_{environment}_{random_string}`

- **Test environment:** `cbk_test_...`
- **Live environment:** `cbk_live_...`

### How to Authenticate

Include your API key in the `X-API-Key` header:

```http
X-API-Key: cbk_test_priiVAF2xCB8vLuIiRuO5wJ6ITQxqp6AZHvR-FKTg-c
```

### Permissions

API keys have two permissions:
- `billing:read` - Read account balances, check credits
- `billing:write` - Create charges, add credits, create accounts

Most keys have both permissions by default.

---

## Account Identity

Accounts are identified using one of these combinations:

1. **OAuth Identity** (recommended):
   ```json
   {
     "oauth_provider": "google",
     "external_id": "user@example.com"
   }
   ```

2. **WhatsApp Identity**:
   ```json
   {
     "oauth_provider": "whatsapp",
     "external_id": "whatsapp_user_id",
     "wa_id": "+1234567890"
   }
   ```

3. **Multi-tenant Identity**:
   ```json
   {
     "oauth_provider": "custom",
     "external_id": "user_id",
     "tenant_id": "org_123"
   }
   ```

---

## API Endpoints

### 1. Health Check

**Endpoint:** `GET /health`

**Authentication:** None required

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2025-10-09T03:54:19.169503+00:00"
}
```

---

### 2. Check Credits

Check if an account has sufficient credits for an operation.

**Endpoint:** `POST /v1/billing/credits/check`

**Required Permission:** `billing:read`

**Request:**
```json
{
  "oauth_provider": "google",
  "external_id": "user@example.com",
  "amount_minor": 100,
  "context": "conversation_message"
}
```

**Response:**
```json
{
  "account_id": "123e4567-e89b-12d3-a456-426614174000",
  "has_sufficient_credits": true,
  "balance_minor": 5000,
  "required_minor": 100,
  "currency": "USD"
}
```

**Status Codes:**
- `200 OK` - Check completed successfully
- `401 Unauthorized` - Invalid or missing API key
- `404 Not Found` - Account does not exist

---

### 3. Create Charge

Deduct credits from an account (consume usage).

**Endpoint:** `POST /v1/billing/charges`

**Required Permission:** `billing:write`

**Request:**
```json
{
  "oauth_provider": "google",
  "external_id": "user@example.com",
  "amount_minor": 100,
  "currency": "USD",
  "description": "AI conversation - 5 messages",
  "metadata": {
    "conversation_id": "conv_123",
    "message_count": 5
  },
  "idempotency_key": "charge_conv123_msg5_20251009"
}
```

**Response:**
```json
{
  "charge_id": "456e7890-e89b-12d3-a456-426614174111",
  "account_id": "123e4567-e89b-12d3-a456-426614174000",
  "amount_minor": 100,
  "currency": "USD",
  "balance_after": 4900,
  "created_at": "2025-10-09T04:00:00.000000+00:00",
  "description": "AI conversation - 5 messages",
  "metadata": {
    "conversation_id": "conv_123",
    "message_count": 5
  }
}
```

**Status Codes:**
- `201 Created` - Charge created successfully
- `402 Payment Required` - Insufficient credits
- `403 Forbidden` - Account suspended or closed
- `404 Not Found` - Account not found
- `409 Conflict` - Idempotency key already used (returns existing charge ID in `X-Existing-Charge-ID` header)

**Idempotency:**
- Use the same `idempotency_key` for retries
- If the charge already exists, you'll get a 409 with the existing charge ID
- Idempotency keys prevent double-charging on network failures

---

### 4. Add Credits

Add credits to an account (top-up, purchase, or admin grant).

**Endpoint:** `POST /v1/billing/credits`

**Required Permission:** `billing:write`

**Request:**
```json
{
  "oauth_provider": "google",
  "external_id": "user@example.com",
  "amount_minor": 2000,
  "currency": "USD",
  "description": "Monthly credit grant",
  "transaction_type": "grant",
  "external_transaction_id": "grant_monthly_202510",
  "idempotency_key": "credit_grant_202510_user123"
}
```

**Transaction Types:**
- `purchase` - User purchased credits (via Stripe, etc.)
- `refund` - Refund for previous charge
- `grant` - Admin/promotional credit grant
- `transfer` - Transfer between accounts

**Response:**
```json
{
  "credit_id": "789e0123-e89b-12d3-a456-426614174222",
  "account_id": "123e4567-e89b-12d3-a456-426614174000",
  "amount_minor": 2000,
  "currency": "USD",
  "balance_after": 6900,
  "transaction_type": "grant",
  "description": "Monthly credit grant",
  "external_transaction_id": "grant_monthly_202510",
  "created_at": "2025-10-09T04:05:00.000000+00:00"
}
```

**Status Codes:**
- `201 Created` - Credits added successfully
- `404 Not Found` - Account not found
- `409 Conflict` - Idempotency key already used

---

### 5. Create or Get Account

Create a new billing account or retrieve an existing one.

**Endpoint:** `POST /v1/billing/accounts`

**Required Permission:** `billing:write`

**Request:**
```json
{
  "oauth_provider": "google",
  "external_id": "newuser@example.com",
  "initial_balance_minor": 500,
  "currency": "USD",
  "plan_name": "free",
  "marketing_opt_in": true,
  "marketing_opt_in_source": "signup_form"
}
```

**Optional Fields:**
- `wa_id` - WhatsApp ID (for WhatsApp users)
- `tenant_id` - Organization/tenant ID (for multi-tenant setups)
- `marketing_opt_in` - GDPR consent for marketing emails
- `marketing_opt_in_source` - Where consent was collected

**Response:**
```json
{
  "account_id": "123e4567-e89b-12d3-a456-426614174000",
  "oauth_provider": "google",
  "external_id": "newuser@example.com",
  "wa_id": null,
  "tenant_id": null,
  "balance_minor": 500,
  "currency": "USD",
  "plan_name": "free",
  "status": "active",
  "marketing_opt_in": true,
  "marketing_opt_in_at": "2025-10-09T04:10:00.000000+00:00",
  "marketing_opt_in_source": "signup_form",
  "created_at": "2025-10-09T04:10:00.000000+00:00",
  "updated_at": "2025-10-09T04:10:00.000000+00:00"
}
```

**Status Codes:**
- `201 Created` - Account created or retrieved
- `500 Internal Server Error` - Database integrity error

**Note:** This endpoint is idempotent. If the account already exists, it returns the existing account without modification.

---

### 6. Get Account

Retrieve account details by identity.

**Endpoint:** `GET /v1/billing/accounts/{oauth_provider}/{external_id}`

**Required Permission:** `billing:read`

**Query Parameters:**
- `wa_id` (optional) - WhatsApp ID
- `tenant_id` (optional) - Tenant ID

**Example Request:**
```
GET /v1/billing/accounts/google/user@example.com
```

**Response:**
```json
{
  "account_id": "123e4567-e89b-12d3-a456-426614174000",
  "oauth_provider": "google",
  "external_id": "user@example.com",
  "wa_id": null,
  "tenant_id": null,
  "balance_minor": 5000,
  "currency": "USD",
  "plan_name": "free",
  "status": "active",
  "marketing_opt_in": false,
  "marketing_opt_in_at": null,
  "marketing_opt_in_source": null,
  "created_at": "2025-10-01T00:00:00.000000+00:00",
  "updated_at": "2025-10-09T04:00:00.000000+00:00"
}
```

**Status Codes:**
- `200 OK` - Account found
- `404 Not Found` - Account does not exist

---

### 7. Create Purchase (Stripe Payment)

Initiate a Stripe payment intent for purchasing credits.

**Endpoint:** `POST /v1/billing/purchases`

**Required Permission:** `billing:write`

**Request:**
```json
{
  "oauth_provider": "google",
  "external_id": "user@example.com",
  "customer_email": "user@example.com"
}
```

**Response:**
```json
{
  "payment_id": "pi_3AbCdEfGhIjKlMnO",
  "client_secret": "pi_3AbCdEfGhIjKlMnO_secret_XyZ123",
  "amount_minor": 500,
  "currency": "USD",
  "uses_purchased": 20,
  "status": "requires_payment_method"
}
```

**Usage:**
1. Call this endpoint to create a payment intent
2. Use the `client_secret` with Stripe.js on your frontend
3. Customer completes payment via Stripe Elements
4. Stripe webhook automatically credits the account upon successful payment

**Status Codes:**
- `201 Created` - Payment intent created
- `503 Service Unavailable` - Stripe is down or misconfigured

---

## Currency & Amounts

All monetary amounts use **minor units** (cents for USD):

- `$1.00 USD` = `100` minor units
- `$5.00 USD` = `500` minor units
- `$0.01 USD` = `1` minor unit

**Example:**
```json
{
  "amount_minor": 500,
  "currency": "USD"
}
```
This represents **$5.00 USD**.

---

## Error Handling

### Standard Error Response

```json
{
  "detail": "Error message describing what went wrong"
}
```

### Common HTTP Status Codes

| Code | Meaning | Action |
|------|---------|--------|
| `200` | Success | Operation completed |
| `201` | Created | Resource created successfully |
| `400` | Bad Request | Fix request parameters |
| `401` | Unauthorized | Check API key |
| `402` | Payment Required | Insufficient credits |
| `403` | Forbidden | Account suspended/closed |
| `404` | Not Found | Account doesn't exist |
| `409` | Conflict | Idempotency conflict (already processed) |
| `500` | Server Error | Retry or contact support |
| `503` | Service Unavailable | External service down (Stripe, etc.) |

### Insufficient Credits Example

```http
HTTP/1.1 402 Payment Required
Content-Type: application/json

{
  "detail": "Insufficient credits. Balance: 50, Required: 100"
}
```

### Idempotency Conflict Example

```http
HTTP/1.1 409 Conflict
X-Existing-Charge-ID: 456e7890-e89b-12d3-a456-426614174111
Content-Type: application/json

{
  "detail": "Charge already exists"
}
```

---

## Best Practices

### 1. Always Use Idempotency Keys

For all write operations (charges, credits, accounts), include an `idempotency_key`:

```json
{
  "idempotency_key": "charge_user123_conv456_20251009"
}
```

**Format suggestions:**
- Charges: `charge_{user_id}_{conversation_id}_{date}`
- Credits: `credit_{type}_{user_id}_{period}`
- Accounts: Usually not needed (endpoint is naturally idempotent)

### 2. Check Credits Before Charging

```python
# 1. Check if user has enough credits
check_response = await check_credits(user_id, amount=100)

if not check_response["has_sufficient_credits"]:
    return "Insufficient credits. Please top up."

# 2. Perform the operation
result = await perform_ai_operation()

# 3. Charge the credits
charge_response = await create_charge(
    user_id,
    amount=100,
    description="AI operation",
    idempotency_key=f"charge_{user_id}_{operation_id}"
)
```

### 3. Handle Errors Gracefully

```python
try:
    charge = await create_charge(...)
except HTTPException as e:
    if e.status_code == 402:
        # Insufficient credits - prompt user to purchase
        return "Your credit balance is low. Visit /purchase to add more credits."
    elif e.status_code == 409:
        # Already charged - this is safe, operation was already processed
        existing_charge_id = e.headers.get("X-Existing-Charge-ID")
        return f"Operation already processed (charge: {existing_charge_id})"
    else:
        # Other error - log and retry
        logger.error(f"Billing error: {e}")
        raise
```

### 4. Use Test Environment for Development

- Use test API keys (`cbk_test_...`) during development
- Test keys don't charge real money
- Switch to live keys (`cbk_live_...`) only in production

---

## Example Integration (Python)

```python
import httpx

class BillingClient:
    def __init__(self, api_key: str, base_url: str = "https://billing.ciris.ai"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }

    async def check_credits(self, user_email: str, amount_minor: int) -> dict:
        """Check if user has sufficient credits"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/credits/check",
                json={
                    "oauth_provider": "google",
                    "external_id": user_email,
                    "amount_minor": amount_minor,
                    "context": "ai_operation"
                },
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()

    async def create_charge(
        self,
        user_email: str,
        amount_minor: int,
        description: str,
        idempotency_key: str
    ) -> dict:
        """Charge credits from user account"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/billing/charges",
                json={
                    "oauth_provider": "google",
                    "external_id": user_email,
                    "amount_minor": amount_minor,
                    "currency": "USD",
                    "description": description,
                    "idempotency_key": idempotency_key
                },
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()

    async def get_account(self, user_email: str) -> dict:
        """Get account balance and details"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/v1/billing/accounts/google/{user_email}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()


# Usage example
billing = BillingClient(api_key="cbk_test_priiVAF2xCB8vLuIiRuO5wJ6ITQxqp6AZHvR-FKTg-c")

# Check credits before operation
check = await billing.check_credits("user@example.com", amount_minor=100)
if check["has_sufficient_credits"]:
    # Perform operation...
    result = await perform_ai_chat()

    # Charge credits
    charge = await billing.create_charge(
        user_email="user@example.com",
        amount_minor=100,
        description="AI chat - 3 messages",
        idempotency_key=f"charge_{user_id}_{conversation_id}"
    )
    print(f"Charged successfully. New balance: {charge['balance_after']} cents")
else:
    print("Insufficient credits!")
```

---

## Rate Limits

Current rate limits (configured in nginx):

- **Billing API:** 60 requests per minute per IP
- **Admin API:** 100 requests per minute per IP
- **OAuth Login:** 5 requests per minute per IP

If you exceed these limits, you'll receive a `429 Too Many Requests` response.

---

## Support

- **API Issues:** Check the [admin dashboard](https://billing.ciris.ai/) for system status
- **Questions:** Contact your CIRIS administrator
- **API Key Management:** Use the admin dashboard to create, rotate, or revoke API keys

---

## Changelog

### 2025-10-09
- Initial release
- HTTPS fully operational
- All endpoints tested and functional
- Test API key available: `cbk_test_priiVAF2xCB8vLuIiRuO5wJ6ITQxqp6AZHvR-FKTg-c`
