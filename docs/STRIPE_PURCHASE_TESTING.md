# Stripe Purchase Testing Guide

Complete guide for testing credit purchases using the Stripe test integration.

## Prerequisites

✅ **Stripe test keys configured** in the billing database
✅ **QA_LAPBUNTU API key** (or any test API key with `billing:write` permission)
✅ **Test account created** (auto-created on first credit check)

---

## Overview: Purchase Flow

```
┌─────────────────┐
│  1. Check       │  Check if user has credits (optional)
│     Credits     │  GET /v1/billing/credits/check
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. Create      │  Create Stripe payment intent
│     Purchase    │  POST /v1/billing/purchases
└────────┬────────┘  Returns: payment_id, client_secret
         │
         ▼
┌─────────────────┐
│  3. Complete    │  User enters card details in frontend
│     Payment     │  (Stripe.js handles this)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  4. Check       │  Poll payment status
│     Status      │  GET /v1/billing/purchases/{payment_id}
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. Stripe      │  Webhook confirms payment
│     Webhook     │  Credits automatically added
└─────────────────┘
```

---

## Current Pricing Configuration

From `app/config.py`:

```python
# Payment pricing (minor units = cents)
price_per_purchase_minor: int = 199  # $1.99
paid_uses_per_purchase: int = 50     # 50 uses per purchase
```

**Each purchase:**
- **Cost:** $1.99 (199 cents)
- **Credits:** 50 uses
- **Currency:** USD

---

## Endpoint 1: Create Purchase

**Creates a Stripe payment intent and returns the client secret for frontend.**

### Request

```bash
POST /v1/billing/purchases
X-API-Key: <your-api-key>
Content-Type: application/json

{
  "oauth_provider": "oauth:google",
  "external_id": "999888777666555444",
  "wa_id": null,
  "tenant_id": null,
  "customer_email": "test@example.com",
  "return_url": "https://your-app.com/payment-complete",
  "user_role": "observer",
  "agent_id": "test_agent",
  "marketing_opt_in": false,
  "marketing_opt_in_source": null
}
```

### Required Fields

- ✅ `oauth_provider` - Must start with `"oauth:"`
- ✅ `external_id` - User's unique identifier
- ✅ `customer_email` - **REQUIRED for purchases** (for Stripe receipt)

### Optional Fields

- `wa_id` - WhatsApp ID
- `tenant_id` - Multi-tenant identifier
- `return_url` - URL to redirect after payment (for hosted checkout)
- `user_role` - User role for tracking
- `agent_id` - Agent making the request
- `marketing_opt_in` - GDPR consent
- `marketing_opt_in_source` - Where consent was obtained

### Response (201 Created)

```json
{
  "payment_id": "pi_3AbCdEfGhIjKlMnO",
  "client_secret": "pi_3AbCdEfGhIjKlMnO_secret_XyZ123",
  "amount_minor": 199,
  "currency": "USD",
  "uses_purchased": 50,
  "status": "requires_payment_method"
}
```

### Response Fields

- `payment_id` - Stripe payment intent ID (save this!)
- `client_secret` - Pass to Stripe.js on frontend
- `amount_minor` - Amount in cents (199 = $1.99)
- `currency` - "USD"
- `uses_purchased` - Number of credits (50)
- `status` - Payment status (initially `"requires_payment_method"`)

### Possible Status Values

| Status | Meaning |
|--------|---------|
| `requires_payment_method` | Waiting for card details |
| `requires_confirmation` | Card added, needs confirmation |
| `processing` | Payment being processed |
| `succeeded` | Payment complete! ✅ |
| `canceled` | Payment canceled |
| `requires_action` | 3D Secure or other action needed |

---

## Endpoint 2: Check Payment Status

**Poll this endpoint to check if the payment completed.**

### Request

```bash
GET /v1/billing/purchases/{payment_id}
X-API-Key: <your-api-key>
```

**Example:**
```bash
GET /v1/billing/purchases/pi_3AbCdEfGhIjKlMnO
X-API-Key: <your-api-key>
```

### Response (200 OK)

```json
{
  "payment_id": "pi_3AbCdEfGhIjKlMnO",
  "client_secret": "pi_3AbCdEfGhIjKlMnO_secret_XyZ123",
  "amount_minor": 199,
  "currency": "USD",
  "uses_purchased": 50,
  "status": "succeeded"
}
```

### Polling Strategy

```javascript
// Frontend polling example
async function pollPaymentStatus(paymentId, maxAttempts = 30) {
  for (let i = 0; i < maxAttempts; i++) {
    const response = await fetch(`/v1/billing/purchases/${paymentId}`, {
      headers: { 'X-API-Key': apiKey }
    });

    const data = await response.json();

    if (data.status === 'succeeded') {
      return { success: true, data };
    }

    if (data.status === 'canceled' || data.status === 'failed') {
      return { success: false, data };
    }

    // Wait 2 seconds before next poll
    await new Promise(resolve => setTimeout(resolve, 2000));
  }

  return { success: false, error: 'Timeout' };
}
```

---

## Testing with cURL

### Step 1: Create Purchase

```bash
curl -X POST 'https://billing.ciris.ai/v1/billing/purchases' \
  -H 'X-API-Key: cbk_test_YOUR_API_KEY_HERE' \
  -H 'Content-Type: application/json' \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "999888777666555444",
    "customer_email": "test@example.com"
  }'
```

**Save the `payment_id` from the response!**

### Step 2: Simulate Payment (Test Mode)

In test mode, you need to use Stripe's test card numbers. Since we can't easily simulate this via cURL, you have two options:

#### Option A: Use Stripe Dashboard (Easiest)

1. Go to https://dashboard.stripe.com/test/payments
2. Find your payment intent by ID
3. Click "Pay" and use test card: `4242 4242 4242 4242`
4. Any future expiry date, any CVC

#### Option B: Use Stripe CLI

```bash
stripe payment_intents confirm pi_3AbCdEfGhIjKlMnO \
  --payment-method pm_card_visa
```

### Step 3: Check Payment Status

```bash
curl -X GET 'https://billing.ciris.ai/v1/billing/purchases/pi_3AbCdEfGhIjKlMnO' \
  -H 'X-API-Key: cbk_test_YOUR_API_KEY_HERE'
```

### Step 4: Verify Credits Added

```bash
curl -X POST 'https://billing.ciris.ai/v1/billing/credits/check' \
  -H 'X-API-Key: cbk_test_YOUR_API_KEY_HERE' \
  -H 'Content-Type: application/json' \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "999888777666555444"
  }'
```

**Expected response after successful purchase:**

```json
{
  "has_credit": true,
  "credits_remaining": null,
  "plan_name": "free",
  "reason": null,
  "free_uses_remaining": 50,  // 50 new credits!
  "total_uses": 3,
  "purchase_required": false
}
```

---

## Stripe Test Cards

Use these test card numbers in Stripe's test mode:

### Successful Payments

| Card Number | Brand | Description |
|-------------|-------|-------------|
| `4242 4242 4242 4242` | Visa | Always succeeds |
| `5555 5555 5555 4444` | Mastercard | Always succeeds |
| `3782 822463 10005` | American Express | Always succeeds |

### Failed Payments

| Card Number | Brand | Result |
|-------------|-------|--------|
| `4000 0000 0000 9995` | Visa | Declined - insufficient funds |
| `4000 0000 0000 9987` | Visa | Declined - lost card |
| `4000 0000 0000 9979` | Visa | Declined - stolen card |

### 3D Secure Required

| Card Number | Brand | Result |
|-------------|-------|--------|
| `4000 0025 0000 3155` | Visa | Requires authentication |
| `4000 0027 6000 3184` | Visa | Authentication required, then succeeds |

**For all test cards:**
- **Expiry:** Any future date (e.g., `12/25`)
- **CVC:** Any 3 digits (e.g., `123`)
- **ZIP:** Any valid ZIP code (e.g., `12345`)

---

## Frontend Integration Example

### Step 1: Create Purchase

```javascript
async function createPurchase(userId, email) {
  const response = await fetch('https://billing.ciris.ai/v1/billing/purchases', {
    method: 'POST',
    headers: {
      'X-API-Key': 'your-api-key',
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      oauth_provider: 'oauth:google',
      external_id: userId,
      customer_email: email,
      return_url: window.location.href
    })
  });

  const data = await response.json();
  return data; // { payment_id, client_secret, ... }
}
```

### Step 2: Initialize Stripe.js

```html
<script src="https://js.stripe.com/v3/"></script>
<div id="payment-element"></div>
<button id="submit-payment">Pay $1.99</button>
```

```javascript
// Initialize Stripe with your publishable key
const stripe = Stripe('pk_test_YOUR_PUBLISHABLE_KEY');

// Create purchase intent
const purchase = await createPurchase(userId, email);

// Setup Stripe Elements
const elements = stripe.elements({
  clientSecret: purchase.client_secret
});

const paymentElement = elements.create('payment');
paymentElement.mount('#payment-element');

// Handle payment submission
document.getElementById('submit-payment').addEventListener('click', async () => {
  const { error, paymentIntent } = await stripe.confirmPayment({
    elements,
    confirmParams: {
      return_url: 'https://your-app.com/payment-complete'
    }
  });

  if (error) {
    console.error('Payment failed:', error);
  } else if (paymentIntent.status === 'succeeded') {
    console.log('Payment succeeded!');
    // Credits automatically added via webhook
  }
});
```

### Step 3: Poll Status (Alternative)

If you don't want to use Stripe.js redirect flow:

```javascript
async function waitForPaymentSuccess(paymentId) {
  for (let i = 0; i < 30; i++) {
    const response = await fetch(
      `https://billing.ciris.ai/v1/billing/purchases/${paymentId}`,
      { headers: { 'X-API-Key': 'your-api-key' } }
    );

    const data = await response.json();

    if (data.status === 'succeeded') {
      alert('Payment successful! 50 credits added.');
      return true;
    }

    await new Promise(resolve => setTimeout(resolve, 2000));
  }

  alert('Payment timeout - please refresh to check status');
  return false;
}
```

---

## Webhook Flow (Automatic)

The billing system automatically handles Stripe webhooks:

### Webhook Endpoint

```
POST https://billing.ciris.ai/v1/billing/webhooks/stripe
```

**This endpoint is called automatically by Stripe when payment succeeds.**

### How It Works

1. User completes payment in Stripe
2. Stripe sends webhook to billing API
3. Billing API verifies webhook signature
4. Billing API confirms payment succeeded
5. **Credits automatically added to user's account**

**You don't need to call anything - it's automatic!**

---

## Troubleshooting

### Error: "Payment provider not configured"

**Cause:** Stripe keys not configured in database

**Solution:** Check Stripe config:

```bash
curl 'https://billing.ciris.ai/admin/provider-config/stripe' \
  -H 'Authorization: Bearer <admin-jwt-token>'
```

Should return:
```json
{
  "provider": "stripe",
  "api_key": "sk_test_...",
  "publishable_key": "pk_test_...",
  "webhook_secret": "whsec_...",
  "created_at": "...",
  "updated_at": "..."
}
```

### Error: "Account not found"

**Cause:** User account doesn't exist yet

**Solution:** Call credit check first to auto-create account:

```bash
curl -X POST 'https://billing.ciris.ai/v1/billing/credits/check' \
  -H 'X-API-Key: your-api-key' \
  -H 'Content-Type: application/json' \
  -d '{"oauth_provider":"oauth:google","external_id":"your-user-id"}'
```

### Payment Shows "Processing" Forever

**Possible causes:**
1. Webhook not received (check Stripe dashboard)
2. Test card requires action (3D Secure)
3. Network issue

**Solution:** Check payment status in Stripe Dashboard:
- https://dashboard.stripe.com/test/payments

### Credits Not Added After Payment

**Check:**
1. Payment status: `GET /v1/billing/purchases/{payment_id}`
2. Webhook delivered: Stripe Dashboard → Webhooks
3. Account balance: `POST /v1/billing/credits/check`

---

## Testing Checklist

- [ ] **Create purchase** - Get payment_id and client_secret
- [ ] **Complete payment** - Use test card `4242 4242 4242 4242`
- [ ] **Check status** - Poll until status = "succeeded"
- [ ] **Verify credits** - Check account has 50 new uses
- [ ] **Test failure** - Try declined card `4000 0000 0000 9995`
- [ ] **Test webhook** - Verify credits added automatically
- [ ] **Test 3D Secure** - Use card `4000 0025 0000 3155`

---

## Complete Test Script

```bash
#!/bin/bash

API_KEY="cbk_test_YOUR_API_KEY_HERE"
BASE_URL="https://billing.ciris.ai"
USER_ID="test_user_$(date +%s)"

echo "=== Step 1: Create Purchase ==="
PURCHASE=$(curl -s -X POST "$BASE_URL/v1/billing/purchases" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:google\",
    \"external_id\": \"$USER_ID\",
    \"customer_email\": \"test@example.com\"
  }")

echo "$PURCHASE" | jq .

PAYMENT_ID=$(echo "$PURCHASE" | jq -r .payment_id)
echo "Payment ID: $PAYMENT_ID"

echo ""
echo "=== Step 2: Manual - Complete Payment ==="
echo "Go to: https://dashboard.stripe.com/test/payments"
echo "Find payment: $PAYMENT_ID"
echo "Click 'Pay' and use card: 4242 4242 4242 4242"
echo ""
read -p "Press Enter after completing payment..."

echo ""
echo "=== Step 3: Check Payment Status ==="
STATUS=$(curl -s -X GET "$BASE_URL/v1/billing/purchases/$PAYMENT_ID" \
  -H "X-API-Key: $API_KEY")

echo "$STATUS" | jq .

echo ""
echo "=== Step 4: Verify Credits Added ==="
CREDITS=$(curl -s -X POST "$BASE_URL/v1/billing/credits/check" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:google\",
    \"external_id\": \"$USER_ID\"
  }")

echo "$CREDITS" | jq .

FREE_USES=$(echo "$CREDITS" | jq -r .free_uses_remaining)
echo ""
echo "✅ Test Complete!"
echo "Free uses remaining: $FREE_USES (should be 50)"
```

---

## Quick Reference

### Create Purchase
```bash
POST /v1/billing/purchases
Required: oauth_provider, external_id, customer_email
Returns: payment_id, client_secret, amount_minor, uses_purchased
```

### Check Status
```bash
GET /v1/billing/purchases/{payment_id}
Returns: payment_id, client_secret, amount_minor, uses_purchased, status
```

### Test Card (Always Succeeds)
```
Card: 4242 4242 4242 4242
Expiry: 12/25
CVC: 123
```

### Current Pricing
```
$1.99 = 50 credits
```

---

## Support

**Stripe Test Dashboard:** https://dashboard.stripe.com/test/payments

**Stripe Test Webhooks:** https://dashboard.stripe.com/test/webhooks

**API Documentation:** https://billing.ciris.ai/docs

---

**Happy Testing!** 🎉
