"""
Tests for Google Play API models (Pydantic validation).
"""

import pytest
from pydantic import ValidationError

from app.models.api import GooglePlayVerifyRequest, GooglePlayVerifyResponse


class TestGooglePlayVerifyRequest:
    """Tests for GooglePlayVerifyRequest validation."""

    def test_valid_request(self):
        """Test creating valid verify request."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_12345",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        assert request.oauth_provider == "oauth:google"
        assert request.external_id == "user_12345"
        assert request.purchase_token == "test_token_12345"
        assert request.product_id == "credits_100"
        assert request.package_name == "ai.ciris.agent"

    def test_valid_request_with_optional_fields(self):
        """Test creating request with optional fields."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_12345",
            wa_id="whatsapp_123",
            tenant_id="tenant_456",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            customer_email="test@example.com",
            user_role="admin",
            agent_id="agent_789",
            marketing_opt_in=True,
            marketing_opt_in_source="oauth_login",
        )

        assert request.wa_id == "whatsapp_123"
        assert request.tenant_id == "tenant_456"
        assert request.customer_email == "test@example.com"
        assert request.user_role == "admin"
        assert request.agent_id == "agent_789"
        assert request.marketing_opt_in is True
        assert request.marketing_opt_in_source == "oauth_login"

    def test_invalid_oauth_provider(self):
        """Test that oauth_provider without 'oauth:' prefix fails."""
        with pytest.raises(ValidationError) as exc_info:
            GooglePlayVerifyRequest(
                oauth_provider="google",  # Missing 'oauth:' prefix
                external_id="user_12345",
                purchase_token="test_token_12345",
                product_id="credits_100",
                package_name="ai.ciris.agent",
            )

        assert 'oauth_provider must start with "oauth:"' in str(exc_info.value)

    def test_invalid_purchase_token_too_short(self):
        """Test that short purchase token fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            GooglePlayVerifyRequest(
                oauth_provider="oauth:google",
                external_id="user_12345",
                purchase_token="short",
                product_id="credits_100",
                package_name="ai.ciris.agent",
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("purchase_token",) for e in errors)

    def test_invalid_purchase_token_empty(self):
        """Test that empty purchase token fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            GooglePlayVerifyRequest(
                oauth_provider="oauth:google",
                external_id="user_12345",
                purchase_token="",
                product_id="credits_100",
                package_name="ai.ciris.agent",
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("purchase_token",) for e in errors)

    def test_missing_required_field(self):
        """Test that missing required field fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            GooglePlayVerifyRequest(  # type: ignore[call-arg]
                oauth_provider="oauth:google",
                external_id="user_12345",
                purchase_token="test_token_12345",
                # Missing product_id
                package_name="ai.ciris.agent",
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("product_id",) for e in errors)

    def test_purchase_token_whitespace_trimmed(self):
        """Test that purchase token whitespace is trimmed."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_12345",
            purchase_token="  test_token_12345  ",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        assert request.purchase_token == "test_token_12345"


class TestGooglePlayVerifyResponse:
    """Tests for GooglePlayVerifyResponse."""

    def test_valid_response(self):
        """Test creating valid verify response."""
        response = GooglePlayVerifyResponse(
            verified=True,
            credits_added=100,
            balance_after=100,
            order_id="GPA.1234-5678-9012",
            purchase_time_millis=1700000000000,
        )

        assert response.verified is True
        assert response.credits_added == 100
        assert response.balance_after == 100
        assert response.order_id == "GPA.1234-5678-9012"
        assert response.purchase_time_millis == 1700000000000
        assert response.already_processed is False

    def test_already_processed_response(self):
        """Test response for already processed purchase."""
        response = GooglePlayVerifyResponse(
            verified=True,
            credits_added=100,
            balance_after=200,
            order_id="GPA.1234-5678-9012",
            purchase_time_millis=1700000000000,
            already_processed=True,
        )

        assert response.verified is True
        assert response.already_processed is True

    def test_minimal_response(self):
        """Test response with only required fields."""
        response = GooglePlayVerifyResponse(
            verified=True,
            credits_added=100,
            balance_after=100,
        )

        assert response.verified is True
        assert response.credits_added == 100
        assert response.balance_after == 100
        assert response.order_id is None
        assert response.purchase_time_millis is None
        assert response.already_processed is False
