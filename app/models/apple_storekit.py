"""
Apple StoreKit domain models - Immutable dataclasses for purchase verification.

NO DICTIONARIES - All data uses strongly typed models.

Apple App Store Server API v2 uses JWS (JSON Web Signature) format for
transaction and notification data.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AppleTransactionInfo:
    """Verified Apple StoreKit transaction information.

    This represents a decoded and verified JWS transaction from the
    App Store Server API.
    """

    transaction_id: str  # Unique transaction identifier
    original_transaction_id: str  # First transaction in subscription chain
    product_id: str  # Product identifier from App Store Connect
    bundle_id: str  # App's bundle ID
    purchase_date: datetime  # When purchase was made
    original_purchase_date: datetime  # When original purchase was made
    quantity: int  # Number of consumables purchased
    type: str  # "Auto-Renewable Subscription", "Non-Consumable", "Consumable"
    environment: str  # "Production" or "Sandbox"
    storefront: str  # Three-letter country code (e.g., "USA")
    storefront_id: str  # Storefront identifier

    # Optional fields
    app_account_token: str | None = None  # UUID linked to user account
    in_app_ownership_type: str | None = None  # "PURCHASED" or "FAMILY_SHARED"
    expires_date: datetime | None = None  # For subscriptions
    revocation_date: datetime | None = None  # If revoked
    revocation_reason: int | None = None  # 0: other, 1: app issue
    is_upgraded: bool = False  # If subscription was upgraded

    def is_valid(self) -> bool:
        """Check if transaction is valid and can be credited."""
        # Transaction is valid if not revoked
        return self.revocation_date is None

    def is_sandbox(self) -> bool:
        """Check if this is a sandbox (test) transaction."""
        return self.environment.lower() == "sandbox"

    def is_consumable(self) -> bool:
        """Check if this is a consumable purchase."""
        return self.type == "Consumable"


@dataclass(frozen=True)
class AppleStoreKitWebhookEvent:
    """Apple App Store Server Notification v2 event.

    Notification types:
    - CONSUMPTION_REQUEST: User requested refund
    - DID_CHANGE_RENEWAL_PREF: User changed subscription
    - DID_CHANGE_RENEWAL_STATUS: User toggled auto-renew
    - DID_FAIL_TO_RENEW: Billing retry failed
    - DID_RENEW: Subscription renewed successfully
    - EXPIRED: Subscription expired
    - GRACE_PERIOD_EXPIRED: Grace period ended
    - OFFER_REDEEMED: Promotional offer redeemed
    - PRICE_INCREASE: Price increase notification
    - REFUND: Refund was issued
    - REFUND_DECLINED: Refund request denied
    - REFUND_REVERSED: Refund was reversed
    - RENEWAL_EXTENDED: Renewal date extended
    - REVOKE: Access revoked (Family Sharing)
    - SUBSCRIBED: Initial subscription
    - TEST: Test notification
    """

    notification_type: str  # e.g., "REFUND", "DID_RENEW"
    subtype: str | None  # e.g., "INITIAL_BUY", "UPGRADE"
    notification_uuid: str  # Unique notification ID
    version: str  # API version (e.g., "2.0")
    signed_date: datetime  # When notification was signed
    transaction_info: AppleTransactionInfo | None  # Transaction details
    environment: str  # "Production" or "Sandbox"

    # For renewal/subscription events
    renewal_info: "AppleRenewalInfo | None" = None

    def is_refund(self) -> bool:
        """Check if this is a refund notification."""
        return self.notification_type == "REFUND"

    def is_renewal(self) -> bool:
        """Check if this is a successful renewal."""
        return self.notification_type == "DID_RENEW"

    def is_test(self) -> bool:
        """Check if this is a test notification."""
        return self.notification_type == "TEST"


@dataclass(frozen=True)
class AppleRenewalInfo:
    """Subscription renewal information from Apple."""

    original_transaction_id: str
    product_id: str
    auto_renew_status: int  # 0: off, 1: on
    expiration_intent: int | None = None  # Why subscription expired
    grace_period_expires_date: datetime | None = None
    is_in_billing_retry_period: bool = False
    offer_identifier: str | None = None
    offer_type: int | None = None  # 1: intro, 2: promo, 3: offer code
    price_increase_status: int | None = None  # 0: not responded, 1: agreed

    def will_renew(self) -> bool:
        """Check if subscription will auto-renew."""
        return self.auto_renew_status == 1


@dataclass(frozen=True)
class AppleStoreKitConfig:
    """Configuration for Apple App Store Server API."""

    key_id: str  # Key ID from App Store Connect
    issuer_id: str  # Issuer ID from App Store Connect
    private_key: str  # Private key (.p8 contents)
    bundle_id: str  # App bundle ID
    environment: str  # "production" or "sandbox"

    @property
    def api_base_url(self) -> str:
        """Get the API base URL for the configured environment."""
        if self.environment.lower() == "sandbox":
            return "https://api.storekit-sandbox.itunes.apple.com"
        return "https://api.storekit.itunes.apple.com"

    def __post_init__(self) -> None:
        """Validate configuration fields."""
        if not self.key_id:
            raise ValueError("StoreKit key_id is required")
        if not self.issuer_id:
            raise ValueError("StoreKit issuer_id is required")
        if not self.private_key:
            raise ValueError("StoreKit private_key is required")
        if not self.bundle_id:
            raise ValueError("StoreKit bundle_id is required")
        if self.environment.lower() not in ("production", "sandbox"):
            raise ValueError("Environment must be 'production' or 'sandbox'")
