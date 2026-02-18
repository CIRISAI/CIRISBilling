"""
Apple StoreKit product catalog configuration.

Maps App Store product IDs to credit amounts.
Product IDs must match those configured in App Store Connect.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppleStoreKitProduct:
    """Apple StoreKit product configuration."""

    product_id: str  # App Store Connect product ID
    credits: int  # Credits to grant on purchase
    name: str  # Display name

    def __post_init__(self) -> None:
        """Validate product configuration."""
        if self.credits <= 0:
            raise ValueError(f"Credits must be positive: {self.credits}")
        if not self.product_id:
            raise ValueError("Product ID required")
        if not self.name:
            raise ValueError("Name required")


# Product catalog (must match App Store Connect configuration)
# Product IDs typically follow reverse-domain format: com.company.app.product
# Pricing: Exactly $0.10 per credit with NO volume discounts
# Credits = floor(price / $0.10) to ensure users always pay at least $0.10/credit
APPLE_STOREKIT_PRODUCTS: dict[str, AppleStoreKitProduct] = {
    "ai.ciris.mobile.credits_100_v1": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_100_v1",
        credits=99,  # $9.99 / $0.10 = 99 credits (no discount)
        name="99 Credits",
    ),
    "ai.ciris.mobile.credits_250_v1": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_250_v1",
        credits=249,  # $24.99 / $0.10 = 249 credits (no discount)
        name="249 Credits",
    ),
    "ai.ciris.mobile.credits_600_v1": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_600_v1",
        credits=599,  # $59.99 / $0.10 = 599 credits (no discount)
        name="599 Credits",
    ),
}


def get_product(product_id: str) -> AppleStoreKitProduct:
    """
    Get product configuration by ID.

    Args:
        product_id: App Store product ID

    Returns:
        Product configuration

    Raises:
        ValueError: If product ID not found
    """
    product = APPLE_STOREKIT_PRODUCTS.get(product_id)
    if not product:
        raise ValueError(f"Unknown product ID: {product_id}")
    return product


def get_credits_for_product(product_id: str) -> int:
    """
    Get number of credits for a product.

    Args:
        product_id: App Store product ID

    Returns:
        Number of credits
    """
    return get_product(product_id).credits
