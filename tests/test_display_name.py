"""
Tests for display_name functionality across the billing system.

Tests cover:
- UserIdentity includes name from Google token
- API models accept display_name
- display_name is properly validated
"""

import pytest
from pydantic import ValidationError

from app.api.dependencies import UserIdentity
from app.models.api import (
    CreateAccountRequest,
    CreateChargeRequest,
    CreditCheckRequest,
    GooglePlayVerifyRequest,
)


class TestUserIdentity:
    """Tests for UserIdentity dataclass."""

    def test_user_identity_with_name(self) -> None:
        """Test UserIdentity includes name field."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="123456789",
            email="user@example.com",
            name="John Doe",
        )
        assert user.name == "John Doe"
        assert user.email == "user@example.com"
        assert user.external_id == "123456789"

    def test_user_identity_name_optional(self) -> None:
        """Test UserIdentity name is optional."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="123456789",
            email="user@example.com",
        )
        assert user.name is None

    def test_user_identity_all_fields_optional_except_required(self) -> None:
        """Test UserIdentity with minimal required fields."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="123456789",
        )
        assert user.name is None
        assert user.email is None


class TestAPIModelsDisplayName:
    """Tests for display_name in API request models."""

    def test_credit_check_request_with_display_name(self) -> None:
        """Test CreditCheckRequest accepts display_name."""
        request = CreditCheckRequest(
            oauth_provider="oauth:google",
            external_id="123456789",
            customer_email="user@example.com",
            display_name="John Doe",
        )
        assert request.display_name == "John Doe"

    def test_credit_check_request_display_name_optional(self) -> None:
        """Test CreditCheckRequest display_name is optional."""
        request = CreditCheckRequest(
            oauth_provider="oauth:google",
            external_id="123456789",
        )
        assert request.display_name is None

    def test_create_charge_request_with_display_name(self) -> None:
        """Test CreateChargeRequest accepts display_name."""
        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="123456789",
            amount_minor=100,
            currency="USD",
            description="Test charge",
            customer_email="user@example.com",
            display_name="Jane Doe",
        )
        assert request.display_name == "Jane Doe"

    def test_create_account_request_with_display_name(self) -> None:
        """Test CreateAccountRequest accepts display_name."""
        request = CreateAccountRequest(
            oauth_provider="oauth:google",
            external_id="123456789",
            customer_email="user@example.com",
            display_name="Test User",
        )
        assert request.display_name == "Test User"

    def test_google_play_verify_request_with_display_name(self) -> None:
        """Test GooglePlayVerifyRequest accepts display_name."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="123456789",
            purchase_token="a" * 20,  # min 10 chars
            product_id="credits_100",
            package_name="ai.ciris.mobile",
            customer_email="user@example.com",
            display_name="Mobile User",
        )
        assert request.display_name == "Mobile User"

    def test_display_name_max_length_validation(self) -> None:
        """Test display_name validates max length."""
        with pytest.raises(ValidationError):
            CreditCheckRequest(
                oauth_provider="oauth:google",
                external_id="123456789",
                display_name="x" * 256,  # exceeds 255 char limit
            )

    def test_display_name_min_length_validation(self) -> None:
        """Test display_name validates min length (empty string not allowed)."""
        with pytest.raises(ValidationError):
            CreditCheckRequest(
                oauth_provider="oauth:google",
                external_id="123456789",
                display_name="",  # empty string not allowed
            )
