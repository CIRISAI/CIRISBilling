"""
Tests for Google Play product catalog.
"""

import pytest

from app.services.google_play_products import (
    GOOGLE_PLAY_PRODUCTS,
    GooglePlayProduct,
    get_credits_for_product,
    get_product,
)


class TestGooglePlayProduct:
    """Tests for GooglePlayProduct validation."""

    def test_valid_product(self):
        """Test creating valid product."""
        product = GooglePlayProduct(
            product_id="credits_100",
            credits=100,
            name="100 Credits",
        )

        assert product.product_id == "credits_100"
        assert product.credits == 100
        assert product.name == "100 Credits"

    def test_invalid_credits_zero(self):
        """Test that zero credits raises ValueError."""
        with pytest.raises(ValueError, match="Credits must be positive"):
            GooglePlayProduct(
                product_id="credits_100",
                credits=0,
                name="100 Credits",
            )

    def test_invalid_credits_negative(self):
        """Test that negative credits raises ValueError."""
        with pytest.raises(ValueError, match="Credits must be positive"):
            GooglePlayProduct(
                product_id="credits_100",
                credits=-100,
                name="100 Credits",
            )

    def test_missing_product_id(self):
        """Test that missing product ID raises ValueError."""
        with pytest.raises(ValueError, match="Product ID required"):
            GooglePlayProduct(
                product_id="",
                credits=100,
                name="100 Credits",
            )

    def test_missing_name(self):
        """Test that missing name raises ValueError."""
        with pytest.raises(ValueError, match="Name required"):
            GooglePlayProduct(
                product_id="credits_100",
                credits=100,
                name="",
            )

    def test_immutable(self):
        """Test that GooglePlayProduct is immutable."""
        product = GooglePlayProduct(
            product_id="credits_100",
            credits=100,
            name="100 Credits",
        )

        with pytest.raises(AttributeError):
            product.credits = 200  # type: ignore[misc]


class TestProductCatalog:
    """Tests for product catalog functions."""

    def test_catalog_contains_expected_products(self):
        """Test that catalog contains expected products."""
        assert "credits_100" in GOOGLE_PLAY_PRODUCTS
        assert "credits_250" in GOOGLE_PLAY_PRODUCTS
        assert "credits_600" in GOOGLE_PLAY_PRODUCTS

    def test_catalog_product_credits(self):
        """Test that catalog products have correct credits."""
        assert GOOGLE_PLAY_PRODUCTS["credits_100"].credits == 99
        assert GOOGLE_PLAY_PRODUCTS["credits_250"].credits == 249
        assert GOOGLE_PLAY_PRODUCTS["credits_600"].credits == 599

    def test_get_product_success(self):
        """Test getting existing product."""
        product = get_product("credits_100")

        assert product.product_id == "credits_100"
        assert product.credits == 99
        assert product.name == "99 Credits"

    def test_get_product_not_found(self):
        """Test getting non-existent product raises ValueError."""
        with pytest.raises(ValueError, match="Unknown product ID: invalid_product"):
            get_product("invalid_product")

    def test_get_credits_for_product_success(self):
        """Test getting credits for existing product."""
        credits = get_credits_for_product("credits_100")
        assert credits == 99

        credits = get_credits_for_product("credits_250")
        assert credits == 249

        credits = get_credits_for_product("credits_600")
        assert credits == 599

    def test_get_credits_for_product_not_found(self):
        """Test getting credits for non-existent product raises ValueError."""
        with pytest.raises(ValueError, match="Unknown product ID"):
            get_credits_for_product("invalid_product")
