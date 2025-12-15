"""
Google Play domain models - Immutable dataclasses for purchase verification.

NO DICTIONARIES - All data uses strongly typed models.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GooglePlayPurchaseToken:
    """Validated Google Play purchase token."""

    token: str
    product_id: str
    package_name: str

    def __post_init__(self) -> None:
        """Validate purchase token fields."""
        if not self.token or len(self.token) < 10:
            raise ValueError("Invalid purchase token")
        if not self.product_id:
            raise ValueError("Product ID required")
        if not self.package_name:
            raise ValueError("Package name required")


@dataclass(frozen=True)
class GooglePlayPurchaseVerification:
    """Result of Google Play purchase verification."""

    order_id: str
    purchase_token: str
    product_id: str
    package_name: str
    purchase_time_millis: int
    purchase_state: int  # 0: purchased, 1: canceled, 2: pending
    acknowledgement_state: int  # 0: not acknowledged, 1: acknowledged
    consumption_state: int  # 0: not consumed, 1: consumed
    purchase_type: int | None = None  # None: real, 0: test, 1: promo, 2: rewarded

    def is_valid(self) -> bool:
        """Check if purchase is valid and can be credited."""
        return self.purchase_state == 0

    def is_test_purchase(self) -> bool:
        """Check if this is a test purchase (license tester account)."""
        return self.purchase_type == 0

    def needs_acknowledgement(self) -> bool:
        """Check if purchase needs acknowledgement."""
        return self.acknowledgement_state == 0

    def needs_consumption(self) -> bool:
        """Check if purchase needs consumption (for consumables)."""
        return self.consumption_state == 0


@dataclass(frozen=True)
class GooglePlayWebhookEvent:
    """Provider-agnostic Google Play webhook event."""

    event_id: str
    event_type: str  # "product_purchased", "product_canceled", etc.
    purchase_token: str
    product_id: str
    package_name: str
    notification_type: int
    event_time_millis: int
