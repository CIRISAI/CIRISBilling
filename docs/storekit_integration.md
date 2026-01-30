# Apple StoreKit Integration Guide

This document describes how to integrate iOS in-app purchases with CIRISBilling using Apple StoreKit 2 and the App Store Server API v2.

## Overview

The integration supports:
- **Apple Sign-In**: User authentication via Apple ID tokens
- **StoreKit 2**: iOS in-app purchase verification and credit fulfillment
- **App Store Server Notifications V2**: Webhook handling for refunds and subscription events

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│   iOS App   │────▶│ CIRISBilling │────▶│ App Store Server API│
│ (StoreKit2) │     │     API      │     │        v2           │
└─────────────┘     └──────────────┘     └─────────────────────┘
       │                   │
       │                   ▼
       │            ┌──────────────┐
       └───────────▶│   Database   │
         (auth)     │  (purchases) │
                    └──────────────┘
```

## Products

| Product ID | Credits | Price | Description |
|------------|---------|-------|-------------|
| `ai.ciris.mobile.credits_100` | 100 | $9.99 | 100 Credits |
| `ai.ciris.mobile.credits_250` | 250 | $24.99 | 250 Credits |
| `ai.ciris.mobile.credits_600` | 600 | $59.99 | 600 Credits |

Pricing: **$0.10 per credit**

## Setup

### 1. App Store Connect Configuration

1. Go to [App Store Connect](https://appstoreconnect.apple.com/)
2. Navigate to **Users and Access** → **Integrations** → **In-App Purchase**
3. Generate a new API key:
   - Click **Generate API Key**
   - Note the **Key ID** (10 characters)
   - Note the **Issuer ID** (UUID format, shown at top)
   - Download the `.p8` private key file (only available once!)

4. Create In-App Purchase products:
   - Go to your app → **In-App Purchases**
   - Create consumable products with the IDs listed above
   - Set prices to match the table above

### 2. Environment Variables

```bash
# Apple Sign-In
APPLE_CLIENT_ID=ai.ciris.mobile           # iOS bundle ID
APPLE_CLIENT_IDS=ai.ciris.mobile          # Comma-separated if multiple apps
APPLE_TEAM_ID=ABC123XYZ0                  # 10-char Team ID from Membership

# Apple StoreKit (App Store Server API)
APPLE_STOREKIT_KEY_ID=ABCD123456          # Key ID from App Store Connect
APPLE_STOREKIT_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
APPLE_STOREKIT_PRIVATE_KEY=LS0tLS1...     # Base64-encoded .p8 file
APPLE_STOREKIT_BUNDLE_ID=ai.ciris.mobile
APPLE_STOREKIT_ENVIRONMENT=sandbox        # "sandbox" or "production"
```

To encode the private key:
```bash
base64 -i AuthKey_XXXXXXXX.p8 | tr -d '\n'
```

### 3. Database Migration

Run the migration to create the `apple_storekit_purchases` table:

```bash
make migrate
# or
alembic upgrade head
```

### 4. Webhook Configuration (Optional)

Configure App Store Server Notifications V2 in App Store Connect:
1. Go to your app → **App Information**
2. Under **App Store Server Notifications**, add your webhook URL:
   ```
   https://billing.yourdomain.com/v1/billing/webhooks/apple-storekit
   ```
3. Select **Version 2** notifications

## API Endpoints

### User-Facing Endpoint (iOS App)

**POST /v1/user/apple-storekit/verify**

Verify a purchase using Apple/Google ID token authentication.

```bash
curl -X POST https://billing.yourdomain.com/v1/user/apple-storekit/verify \
  -H "Authorization: Bearer {apple_id_token}" \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "2000000123456789"
  }'
```

Response:
```json
{
  "success": true,
  "credits_added": 100,
  "new_balance": 150,
  "already_processed": false
}
```

### Service-to-Service Endpoint

**POST /v1/billing/apple-storekit/verify**

Verify a purchase using API key authentication.

```bash
curl -X POST https://billing.yourdomain.com/v1/billing/apple-storekit/verify \
  -H "X-API-Key: {api_key}" \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:apple",
    "external_id": "001234.abcd1234...",
    "transaction_id": "2000000123456789"
  }'
```

Response:
```json
{
  "success": true,
  "credits_added": 100,
  "new_balance": 150,
  "transaction_id": "2000000123456789",
  "product_id": "ai.ciris.mobile.credits_100",
  "already_processed": false
}
```

### Webhook Endpoint

**POST /v1/billing/webhooks/apple-storekit**

Receives App Store Server Notifications V2. No authentication required (Apple signs the payload).

Handled notification types:
- `REFUND` - Logs for manual review (credit clawback not yet implemented)
- `TEST` - Test notifications
- All other types are acknowledged and logged

## iOS Implementation

### Swift Example (StoreKit 2)

```swift
import StoreKit

class PurchaseManager {

    // Verify purchase with backend
    func verifyPurchase(transaction: Transaction) async throws -> Bool {
        guard let idToken = try await getAppleIDToken() else {
            throw PurchaseError.notAuthenticated
        }

        let url = URL(string: "https://billing.yourdomain.com/v1/user/apple-storekit/verify")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("Bearer \(idToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = ["transaction_id": String(transaction.id)]
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await URLSession.shared.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse,
              httpResponse.statusCode == 200 else {
            throw PurchaseError.verificationFailed
        }

        let result = try JSONDecoder().decode(VerifyResponse.self, from: data)
        return result.success
    }

    // Listen for transactions
    func listenForTransactions() -> Task<Void, Error> {
        return Task.detached {
            for await result in Transaction.updates {
                do {
                    let transaction = try self.checkVerified(result)

                    // Verify with backend
                    let verified = try await self.verifyPurchase(transaction: transaction)

                    if verified {
                        // Finish the transaction
                        await transaction.finish()
                    }
                } catch {
                    print("Transaction verification failed: \(error)")
                }
            }
        }
    }
}

struct VerifyResponse: Codable {
    let success: Bool
    let credits_added: Int
    let new_balance: Int
    let already_processed: Bool
}
```

## Idempotency

The system is fully idempotent:
- Each `transaction_id` is stored in the `apple_storekit_purchases` table
- Duplicate verification requests return the original result with `already_processed: true`
- Credits are only added once per transaction

## Testing

### Sandbox Testing

1. Set `APPLE_STOREKIT_ENVIRONMENT=sandbox`
2. Use a Sandbox Apple ID in your iOS app
3. Transactions will use sandbox App Store Server API

### Test Notification

Request a test notification from Apple:
```bash
# This is called internally - useful for debugging
POST /inApps/v1/notifications/test
```

## Troubleshooting

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Apple StoreKit provider not configured` | Missing env vars | Set all `APPLE_STOREKIT_*` variables |
| `Transaction not found` | Invalid transaction ID | Verify the transaction ID is correct |
| `Invalid API credentials` | Wrong key/issuer | Check Key ID and Issuer ID |
| `Unknown product ID` | Product not in catalog | Add product to `apple_storekit_products.py` |

### Logs

Check logs for debugging:
```bash
# In Docker
docker logs cirisbilling-api -f | grep apple_storekit
```

Key log events:
- `apple_storekit_provider_initialized` - Provider started
- `apple_storekit_purchase_verified` - Successful verification
- `apple_storekit_purchase_already_processed` - Duplicate request
- `apple_storekit_verification_failed` - Verification error

## Security

- Private keys are stored as environment variables (never in code)
- All API calls use JWT authentication (ES256)
- Webhook payloads are JWS-signed by Apple
- HTTPS is required for all endpoints
- Transaction IDs are unique and prevent replay attacks
