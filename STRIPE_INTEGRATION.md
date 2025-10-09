# Stripe Payment Integration Guide

Complete guide for integrating Stripe payments with CIRIS Billing API.

## Overview

The CIRIS Billing API now supports Stripe for payment processing while maintaining provider-agnostic architecture. Users get:

- **3 free uses** per account
- **$5 for 20 uses** after free tier exhausted

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────┐
│   Client    │         │  CIRIS Billing   │         │   Stripe    │
│  (Agent)    │────────>│      API         │────────>│   Payment   │
└─────────────┘         └──────────────────┘         └─────────────┘
                                │
                                │
                        ┌───────▼───────┐
                        │   PostgreSQL  │
                        │   Database    │
                        └───────────────┘
```

**Provider-Agnostic Design**: All payment operations go through `PaymentProvider` protocol. Stripe is just one implementation - you can add Square, PayPal, or any provider by implementing the same interface.

---

## Setup Instructions

### 1. Create Stripe Account

1. Go to https://stripe.com and create an account
2. Verify your email and complete account setup
3. Navigate to **Developers** → **API keys**

### 2. Get API Keys

You'll need three keys from Stripe:

#### Test Mode (for development)
- **Publishable Key**: `pk_test_...` (for frontend)
- **Secret Key**: `sk_test_...` (for backend)
- **Webhook Secret**: `whsec_...` (for webhook verification)

#### Live Mode (for production)
- **Publishable Key**: `pk_live_...`
- **Secret Key**: `sk_live_...`
- **Webhook Secret**: `whsec_...`

**Getting Keys:**
1. **API Keys**: Developers → API keys (copy both publishable and secret)
2. **Webhook Secret**: Developers → Webhooks → Add endpoint → Copy signing secret

### 3. Configure Environment Variables

Create or update `.env` file in your project root:

```bash
# Stripe Configuration
STRIPE_API_KEY=sk_test_your_secret_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret_here
STRIPE_PUBLISHABLE_KEY=pk_test_your_publishable_key_here

# Pricing Configuration (defaults shown)
FREE_USES_PER_ACCOUNT=3
PAID_USES_PER_PURCHASE=20
PRICE_PER_PURCHASE_MINOR=500  # $5.00 in cents
```

**Security Notes:**
- NEVER commit `.env` to git
- Add `.env` to `.gitignore`
- Use different keys for development/staging/production
- Rotate keys if compromised

### 4. Set Up Webhook Endpoint

#### Option A: Local Development (ngrok)

```bash
# Install ngrok
brew install ngrok  # macOS
# or download from https://ngrok.com/download

# Start your API
make test-local

# In another terminal, expose webhook endpoint
ngrok http 8000

# Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
```

#### Option B: Production Deployment

Use your actual domain:
```
https://billing.yourdomain.com/v1/billing/webhooks/stripe
```

#### Configure in Stripe Dashboard

1. Go to **Developers** → **Webhooks**
2. Click **Add endpoint**
3. Enter webhook URL:
   - Local: `https://abc123.ngrok.io/v1/billing/webhooks/stripe`
   - Production: `https://billing.yourdomain.com/v1/billing/webhooks/stripe`
4. Select events to send:
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
5. Click **Add endpoint**
6. Copy the **Signing secret** (starts with `whsec_`)
7. Update `STRIPE_WEBHOOK_SECRET` in `.env`

### 5. Install Dependencies

```bash
# Install Stripe SDK
pip install stripe==11.1.1

# Or use requirements.txt
pip install -r requirements.txt
```

### 6. Run Database Migrations

```bash
# Apply usage tracking migration
make test-local  # This runs migrations automatically

# Or manually:
docker-compose -f docker-compose.local.yml exec billing-api alembic upgrade head
```

### 7. Verify Setup

```bash
# Check environment variables are loaded
curl http://localhost:8000/health

# Check Stripe connection (will fail if keys are invalid)
curl -X POST http://localhost:8000/v1/billing/purchases \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test@example.com",
    "customer_email": "test@example.com"
  }'
```

---

## API Usage

### 1. Check Credit (Free Tier)

New accounts automatically get 3 free uses:

```bash
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "new-user@example.com",
    "context": {}
  }'
```

**Response (New User):**
```json
{
  "has_credit": true,
  "credits_remaining": 0,
  "plan_name": "free",
  "reason": null,
  "free_uses_remaining": 3,
  "total_uses": 0,
  "purchase_required": false
}
```

**Response (Free Uses Exhausted):**
```json
{
  "has_credit": false,
  "credits_remaining": 0,
  "plan_name": "free",
  "reason": "No free uses or credits remaining",
  "free_uses_remaining": 0,
  "total_uses": 3,
  "purchase_required": true,
  "purchase_price_minor": 500,
  "purchase_uses": 20
}
```

### 2. Create Charge (Use Free Tier)

```bash
curl -X POST http://localhost:8000/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Agent interaction",
    "metadata": {
      "message_id": "msg-001",
      "agent_id": "datum"
    }
  }'
```

**Behavior:**
- First 3 uses: Deducts from `free_uses_remaining`, balance unchanged
- After free uses: Deducts from `balance_minor` (paid credits)

### 3. Purchase Additional Uses

**Step 1: Create Payment Intent**

```bash
curl -X POST http://localhost:8000/v1/billing/purchases \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "user@example.com",
    "customer_email": "user@example.com"
  }'
```

**Response:**
```json
{
  "payment_id": "pi_1234567890abcdef",
  "client_secret": "pi_1234567890abcdef_secret_xyz",
  "amount_minor": 500,
  "currency": "USD",
  "uses_purchased": 20,
  "status": "requires_payment_method"
}
```

**Step 2: Complete Payment on Frontend**

Use Stripe.js or Stripe Elements to complete payment with `client_secret`:

```javascript
// Frontend code (React/Vue/vanilla JS)
const stripe = Stripe('pk_test_your_publishable_key');

const {error, paymentIntent} = await stripe.confirmCardPayment(
  clientSecret,
  {
    payment_method: {
      card: cardElement,
      billing_details: {
        email: 'user@example.com'
      }
    }
  }
);

if (error) {
  console.error('Payment failed:', error.message);
} else if (paymentIntent.status === 'succeeded') {
  console.log('Payment successful! 20 uses added.');
}
```

**Step 3: Webhook Confirms Payment**

Stripe sends webhook to `/v1/billing/webhooks/stripe` when payment succeeds. The API automatically:
1. Verifies webhook signature
2. Confirms payment with Stripe
3. Adds 20 uses to account balance
4. Logs transaction with `external_transaction_id`

---

## Payment Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  User Exhausts Free Tier (3 uses)                               │
└──────────────────┬──────────────────────────────────────────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Credit Check API   │
         │  Returns:           │
         │  purchase_required: │
         │    true             │
         │  price: $5          │
         │  uses: 20           │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Frontend Shows     │
         │  Purchase Prompt    │
         │  "Buy 20 uses for   │
         │   $5?"              │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  User Clicks        │
         │  "Purchase"         │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  POST /purchases    │
         │  Returns:           │
         │  - payment_id       │
         │  - client_secret    │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Frontend Collects  │
         │  Card Details       │
         │  (Stripe.js)        │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Stripe Processes   │
         │  Payment            │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  Stripe Sends       │
         │  Webhook            │
         │  (payment_intent.   │
         │   succeeded)        │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  API Verifies       │
         │  Webhook Signature  │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  API Adds 20 Uses   │
         │  to Account         │
         │  (as balance)       │
         └─────────┬───────────┘
                   │
                   ▼
         ┌─────────────────────┐
         │  User Can Continue  │
         │  Using Agent        │
         └─────────────────────┘
```

---

## Testing

### Test Credit Cards

Stripe provides test cards for development:

| Card Number         | Description           |
|--------------------|-----------------------|
| 4242 4242 4242 4242 | Successful payment    |
| 4000 0000 0000 9995 | Declined (insufficient funds) |
| 4000 0025 0000 3155 | Requires authentication (3D Secure) |

- **Expiry**: Any future date (e.g., 12/25)
- **CVC**: Any 3 digits (e.g., 123)
- **ZIP**: Any 5 digits (e.g., 12345)

### Manual Testing

```bash
# 1. Start local stack
make test-local

# 2. Create new account (gets 3 free uses automatically)
curl -X POST http://localhost:8000/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "initial_balance_minor": 0,
    "currency": "USD",
    "plan_name": "free"
  }'

# 3. Check credit (should show 3 free uses)
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "context": {}
  }'

# 4. Use first free use
curl -X POST http://localhost:8000/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Free use test",
    "metadata": {}
  }'

# 5. Check credit again (should show 2 free uses remaining)
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "context": {}
  }'

# 6. Initiate purchase
curl -X POST http://localhost:8000/v1/billing/purchases \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "customer_email": "stripe-test@example.com"
  }'

# 7. Complete payment in Stripe Dashboard or using frontend
#    - Go to Stripe Dashboard → Payments
#    - Find the payment intent
#    - Mark as succeeded (test mode)

# 8. Webhook fires automatically, adds 20 uses to balance

# 9. Check credit (should show paid balance)
curl -X POST http://localhost:8000/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "stripe-test@example.com",
    "context": {}
  }'
```

### Automated Tests

```bash
# Run E2E tests (includes purchase flow)
make test-e2e-python
```

---

## Monitoring

### Logs

```bash
# View payment-related logs
docker-compose -f docker-compose.local.yml logs billing-api | \
  jq 'select(.event | contains("stripe") or contains("purchase"))'

# Watch webhook events
docker-compose -f docker-compose.local.yml logs -f billing-api | \
  jq 'select(.event | startswith("stripe_webhook"))'
```

### Metrics

Query in Prometheus (http://localhost:9090):

```promql
# Purchase rate
rate(billing_credit_checks_total{purchase_required="true"}[5m])

# Payment success rate
rate(billing_charges_total{success="True"}[5m])
```

### Stripe Dashboard

Monitor in real-time:
- **Payments**: View all payment intents
- **Logs**: See webhook delivery status
- **Balance**: Track revenue
- **Disputes**: Handle chargebacks

---

## Troubleshooting

### Webhook Not Receiving Events

**Check 1: Verify webhook is registered**
```bash
# List webhooks in Stripe
curl https://api.stripe.com/v1/webhook_endpoints \
  -u sk_test_your_secret_key:
```

**Check 2: Check ngrok is running (local dev)**
```bash
curl https://your-ngrok-url.ngrok.io/health
```

**Check 3: View webhook logs in Stripe Dashboard**
- Developers → Webhooks → Select endpoint → View logs

**Check 4: Check API logs**
```bash
docker-compose -f docker-compose.local.yml logs billing-api | \
  grep "stripe_webhook"
```

### Payment Intent Failing

**Error: "Invalid API Key"**
- Check `STRIPE_API_KEY` starts with `sk_test_` or `sk_live_`
- Verify key is set in environment
- Restart API after updating env vars

**Error: "Amount must be at least $0.50"**
- Stripe minimum is $0.50 USD
- Our default is $5.00 (500 cents) ✓

**Error: "No such payment_intent"**
- Payment ID is invalid
- Check you're using correct API keys (test vs live)

### Webhook Signature Verification Failed

**Error: "Invalid webhook signature"**
- Check `STRIPE_WEBHOOK_SECRET` is correct
- Verify it starts with `whsec_`
- Webhook secret is different per endpoint
- Get correct secret from Stripe Dashboard

### Account Not Receiving Credits After Payment

**Check 1: Verify webhook was received**
```bash
docker-compose -f docker-compose.local.yml logs billing-api | \
  grep "stripe_webhook_received"
```

**Check 2: Check payment status in Stripe**
- Dashboard → Payments → Find payment
- Status should be "Succeeded"

**Check 3: Query database directly**
```sql
SELECT * FROM credits
WHERE external_transaction_id LIKE 'pi_%'
ORDER BY created_at DESC
LIMIT 10;
```

---

## Production Checklist

Before going live:

- [ ] Switch to Stripe **Live Mode** keys
- [ ] Update `STRIPE_API_KEY` to `sk_live_...`
- [ ] Update `STRIPE_PUBLISHABLE_KEY` to `pk_live_...`
- [ ] Create production webhook endpoint
- [ ] Update `STRIPE_WEBHOOK_SECRET` with production secret
- [ ] Enable HTTPS on your domain
- [ ] Set webhook URL to production domain
- [ ] Test payment with live card (small amount)
- [ ] Monitor webhook delivery in Stripe Dashboard
- [ ] Set up alerts for failed webhooks
- [ ] Enable Stripe Radar (fraud detection)
- [ ] Configure Stripe email receipts
- [ ] Test refund flow
- [ ] Document disaster recovery procedure

---

## Security Best Practices

1. **API Keys**
   - Never commit keys to git
   - Use environment variables
   - Rotate keys quarterly
   - Use separate keys per environment

2. **Webhooks**
   - Always verify webhook signatures
   - Use HTTPS in production
   - Log all webhook events
   - Implement retry logic

3. **Payments**
   - Never store card details
   - Let Stripe handle PCI compliance
   - Use HTTPS for all requests
   - Implement idempotency keys

4. **Customer Data**
   - Store minimal customer info
   - Hash/encrypt sensitive data
   - Comply with GDPR/CCPA
   - Implement data deletion

---

## Provider-Agnostic Architecture

The billing system remains provider-agnostic. To add another payment provider:

### 1. Implement PaymentProvider Protocol

```python
# app/services/square_provider.py
from app.services.payment_provider import PaymentProvider

class SquareProvider:
    """Square payment provider implementation."""

    async def create_payment_intent(self, intent: PaymentIntent) -> PaymentResult:
        # Implement Square payment creation
        pass

    async def confirm_payment(self, payment_id: str) -> bool:
        # Implement Square payment confirmation
        pass

    async def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        # Implement Square webhook verification
        pass

    async def refund_payment(self, payment_id: str, amount_minor: int | None) -> str:
        # Implement Square refund
        pass
```

### 2. Add Configuration

```python
# app/config.py
class Settings(BaseSettings):
    # Existing Stripe config...

    # Add Square config
    square_access_token: str = ""
    square_location_id: str = ""
    square_webhook_signature_key: str = ""

    # Provider selection
    payment_provider: str = "stripe"  # or "square"
```

### 3. Use Dependency Injection

```python
# app/api/routes.py
def get_payment_provider() -> PaymentProvider:
    if settings.payment_provider == "stripe":
        return StripeProvider(...)
    elif settings.payment_provider == "square":
        return SquareProvider(...)
    else:
        raise ValueError(f"Unknown provider: {settings.payment_provider}")

@router.post("/v1/billing/purchases")
async def create_purchase(
    request: PurchaseRequest,
    payment_provider: PaymentProvider = Depends(get_payment_provider),
):
    # Use payment_provider (works with any provider)
    payment_result = await payment_provider.create_payment_intent(intent)
```

---

## Support

### Stripe Documentation
- API Reference: https://docs.stripe.com/api
- Webhooks: https://docs.stripe.com/webhooks
- Testing: https://docs.stripe.com/testing

### CIRIS Billing
- API Docs: http://localhost:8000/docs
- GitHub: https://github.com/yourusername/CIRISBilling
- Issues: https://github.com/yourusername/CIRISBilling/issues

---

## Summary

**Pricing**: 3 free uses, then $5 for 20 uses

**Setup Steps**:
1. Create Stripe account
2. Get API keys (secret, publishable, webhook)
3. Configure `.env` file
4. Set up webhook endpoint
5. Run migrations
6. Test with test cards

**Key Endpoints**:
- `POST /v1/billing/credits/check` - Check available uses
- `POST /v1/billing/charges` - Use a credit (free or paid)
- `POST /v1/billing/purchases` - Initiate purchase
- `POST /v1/billing/webhooks/stripe` - Receive payment confirmation

**Architecture**: Provider-agnostic design allows switching between Stripe, Square, PayPal, etc. by implementing `PaymentProvider` protocol.

🎉 **You're all set!** Your billing system is now ready to accept payments through Stripe.
