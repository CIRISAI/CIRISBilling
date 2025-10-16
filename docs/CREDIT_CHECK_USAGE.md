# Credit Check API - Email and Marketing Consent

## Overview

The credit check endpoint now accepts **customer_email** and **marketing_opt_in** fields to capture user contact information and marketing preferences.

## Endpoint

```
POST /v1/billing/credits/check
```

## Required Fields

- `oauth_provider`: OAuth provider (must start with `"oauth:"`, e.g., `"oauth:google"`, `"oauth:test"`)
- `external_id`: Unique user ID from your system

## New Optional Fields

### customer_email
- **Type:** string (optional)
- **Max length:** 255 characters
- **Description:** User's email address for receipts and notifications
- **When to send:** Always send when available, especially on first credit check
- **Use case:** Used for Stripe payment receipts and future communications

### marketing_opt_in
- **Type:** boolean (default: `false`)
- **Description:** Whether user consented to marketing communications (GDPR compliance)
- **When to send:** Only set to `true` if user explicitly opted in

### marketing_opt_in_source
- **Type:** string (optional)
- **Max length:** 50 characters
- **Description:** Source of the marketing consent
- **Common values:**
  - `"oauth_login"` - User opted in during OAuth login
  - `"settings"` - User opted in via settings page
  - `"checkout"` - User opted in during checkout
  - `"signup"` - User opted in during account creation

## SDK Usage Example

```python
from ciris_sdk import CIRISClient

client = CIRISClient(
    base_url="https://billing.ciris.ai",
    api_key="cbk_test_..."
)

# Check credits with email and marketing consent
response = client.billing.check_credits(
    oauth_provider="oauth:google",
    external_id="user_12345",
    customer_email="user@example.com",  # NEW: User's email
    marketing_opt_in=True,  # NEW: User consented to marketing
    marketing_opt_in_source="oauth_login",  # NEW: Where they opted in
    user_role="observer",  # Optional: User's role
    agent_id="agent_qa_v1"  # Optional: Agent making the request
)

print(f"Has credit: {response.has_credit}")
print(f"Free uses remaining: {response.free_uses_remaining}")
```

## Request Example (cURL)

```bash
curl -X POST 'https://billing.ciris.ai/v1/billing/credits/check' \
  -H 'X-API-Key: cbk_test_...' \
  -H 'Content-Type: application/json' \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user_12345",
    "customer_email": "user@example.com",
    "marketing_opt_in": true,
    "marketing_opt_in_source": "oauth_login",
    "user_role": "observer",
    "agent_id": "agent_qa_v1"
  }'
```

## Response

```json
{
  "has_credit": true,
  "credits_remaining": 0,
  "free_uses_remaining": 3,
  "total_uses": 0,
  "plan_name": "free",
  "purchase_required": false
}
```

## Behavior

### First Credit Check (New User)
- If `customer_email` is provided, it's stored with the user account
- If `marketing_opt_in=true`, consent is recorded with timestamp and source
- User is auto-created with free credits (default: 3 uses)

### Subsequent Credit Checks
- Email and marketing preferences are preserved from first check
- Can be updated by sending new values in future requests

## GDPR Compliance

The marketing consent fields ensure GDPR compliance:

1. **Explicit Consent**: Only set `marketing_opt_in=true` when user explicitly opted in
2. **Audit Trail**: `marketing_opt_in_source` tracks where consent was obtained
3. **Timestamp**: `marketing_opt_in_at` is automatically recorded
4. **Default Opt-Out**: Default is `false` (no marketing)

## Best Practices

1. **Always send email on first check**: Helps with payment receipts later
2. **Respect user choice**: Only set `marketing_opt_in=true` if they explicitly consented
3. **Be specific about source**: Use descriptive `marketing_opt_in_source` values
4. **Update when changed**: If user changes email/preferences, send updated values

## Related Fields

These fields are also accepted by:
- `POST /v1/billing/purchases` - Purchase endpoint
- `POST /v1/billing/accounts` - Account creation
- `POST /v1/billing/charges` - Charge creation
- `POST /v1/billing/credits` - Credit addition

##Account Storage

When you provide these fields, they're stored in the user's account:
- `customer_email` - Stored as-is
- `marketing_opt_in` - Boolean flag
- `marketing_opt_in_at` - Timestamp when consent was given
- `marketing_opt_in_source` - Where consent came from
- `user_role` - User's role in your system
- `agent_id` - Which agent made the request

You can retrieve this information later via:
```
GET /v1/billing/accounts/{oauth_provider}/{external_id}
```

## Questions?

For questions or issues, please open an issue at https://github.com/CIRISAI/CIRISBilling/issues
