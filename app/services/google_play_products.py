"""
Google Play product catalog configuration.

Maps Google Play product IDs to credit amounts.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GooglePlayProduct:
    """Google Play product configuration."""

    product_id: str
    credits: int
    name: str

    def __post_init__(self) -> None:
        """Validate product configuration."""
        if self.credits <= 0:
            raise ValueError(f"Credits must be positive: {self.credits}")
        if not self.product_id:
            raise ValueError("Product ID required")
        if not self.name:
            raise ValueError("Name required")


# Product catalog (must match Google Play Console configuration)
GOOGLE_PLAY_PRODUCTS: dict[str, GooglePlayProduct] = {
    "credits_100": GooglePlayProduct(
        product_id="credits_100",
        credits=100,
        name="100 Credits",
    ),
    "credits_250": GooglePlayProduct(
        product_id="credits_250",
        credits=250,
        name="250 Credits",
    ),
    "credits_600": GooglePlayProduct(
        product_id="credits_600",
        credits=600,
        name="600 Credits",
    ),
}


def get_product(product_id: str) -> GooglePlayProduct:
    """
    Get product configuration by ID.

    Args:
        product_id: Google Play product ID

    Returns:
        Product configuration

    Raises:
        ValueError: If product ID not found
    """
    product = GOOGLE_PLAY_PRODUCTS.get(product_id)
    if not product:
        raise ValueError(f"Unknown product ID: {product_id}")
    return product


def get_credits_for_product(product_id: str) -> int:
    """
    Get number of credits for a product.

    Args:
        product_id: Google Play product ID

    Returns:
        Number of credits
    """
    return get_product(product_id).credits
