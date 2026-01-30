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
# Pricing: $0.10 per credit
APPLE_STOREKIT_PRODUCTS: dict[str, AppleStoreKitProduct] = {
    "ai.ciris.mobile.credits_100": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_100",
        credits=100,
        name="100 Credits",  # $9.99
    ),
    "ai.ciris.mobile.credits_250": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_250",
        credits=250,
        name="250 Credits",  # $24.99
    ),
    "ai.ciris.mobile.credits_600": AppleStoreKitProduct(
        product_id="ai.ciris.mobile.credits_600",
        credits=600,
        name="600 Credits",  # $59.99
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
