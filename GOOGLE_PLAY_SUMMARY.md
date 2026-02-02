# Google Play Billing Integration - Complete

Clean, minimal implementation following existing Stripe patterns.

## Test Results

**57 tests passing** across 5 test files:
- `test_google_play_models.py` - 13 tests
- `test_google_play_api_models.py` - 10 tests
- `test_google_play_products.py` - 12 tests
- `test_google_play_migration.py` - 14 tests
- `test_google_play_routes.py` - 8 tests

## Files Created/Modified

### Domain Models
- **`app/models/google_play.py`** - Immutable dataclasses
  - `GooglePlayPurchaseToken` - Validated purchase token
  - `GooglePlayPurchaseVerification` - Verification result with helpers
  - `GooglePlayWebhookEvent` - Provider-agnostic webhook event

### API Models
- **`app/models/api.py`** - Added Pydantic models
  - `GooglePlayVerifyRequest` - Purchase verification request
  - `GooglePlayVerifyResponse` - Purchase verification response

### Database
- **`app/db/models.py`** - Added `GooglePlayPurchase` ORM model
- **`alembic/versions/2025_11_25_0009-add_google_play_support.py`** - Migration
  - Creates `google_play_purchases` table
  - Unique index on `purchase_token` (idempotency)
  - Updates `provider_configs` constraint for `google_play`

### Services
- **`app/services/google_play_provider.py`** - Provider implementation
  - `verify_purchase()` - Call Google Play API
  - `consume_purchase()` - Mark as used
  - `acknowledge_purchase()` - Acknowledge within 3 days
  - `verify_webhook()` - Parse Real-Time Developer Notifications

- **`app/services/google_play_products.py`** - Product catalog
  - `credits_100` → 100 credits
  - `credits_250` → 250 credits
  - `credits_600` → 600 credits

- **`app/services/provider_config.py`** - Added `get_google_play_config()`

### API Routes
- **`app/api/routes.py`** - Added endpoints
  - `POST /v1/billing/google-play/verify` - Verify and credit
  - `POST /v1/billing/webhooks/google-play` - Handle RTDN

## API Flow

```
1. Android app → Google Play purchase (via Billing Library)
2. App receives purchase_token
3. App → POST /v1/billing/google-play/verify
   {
     "oauth_provider": "oauth:google",
     "external_id": "user_123",
     "purchase_token": "...",
     "product_id": "credits_100",
     "package_name": "ai.ciris.mobile"
   }
4. Backend → Verify with Google Play API
5. Backend → Check idempotency (unique purchase_token)
6. Backend → Add credits to account
7. Backend → Consume purchase (prevents reuse)
8. Return → { verified: true, credits_added: 100, balance_after: 100 }
```

## Database Schema

```sql
CREATE TABLE google_play_purchases (
    id BIGSERIAL PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    purchase_token VARCHAR(4096) NOT NULL UNIQUE,
    order_id VARCHAR(255) NOT NULL,
    product_id VARCHAR(255) NOT NULL,
    package_name VARCHAR(255) NOT NULL,
    purchase_time_millis BIGINT NOT NULL,
    purchase_state INTEGER NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    credits_added INTEGER NOT NULL,
    credit_id BIGINT REFERENCES credits(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_google_play_purchases_purchase_token ON google_play_purchases(purchase_token);
CREATE INDEX idx_google_play_purchases_order_id ON google_play_purchases(order_id);
CREATE INDEX idx_google_play_purchases_account_id ON google_play_purchases(account_id);
```

## Key Features

1. **Idempotency** - Unique constraint on `purchase_token` prevents duplicate credits
2. **Write Verification** - Same patterns as Stripe
3. **Graceful Degradation** - Credits added even if consumption fails
4. **Audit Trail** - Links to `credits` table via `credit_id`

## Deployment Checklist

1. [ ] Run migration: `alembic upgrade head`
2. [ ] Add Google Play config to `provider_configs`:
   ```sql
   INSERT INTO provider_configs (provider_type, is_active, config_data)
   VALUES ('google_play', true, '{
     "service_account_json": "/path/to/service-account.json",
     "package_name": "ai.ciris.mobile"
   }');
   ```
3. [ ] Configure Google Play Console products (credits_100, credits_250, credits_600)
4. [ ] Set up service account with Android Publisher API access
5. [ ] Configure RTDN webhook URL

## Next Steps (Optional)

- Subscription support (monthly credits)
- Refund handling (credit clawback)
- Webhook signature verification (JWT from Pub/Sub)
