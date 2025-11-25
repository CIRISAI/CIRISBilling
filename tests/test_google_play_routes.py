"""
Tests for Google Play API routes.
"""

import pytest

from app.models.api import GooglePlayVerifyRequest, GooglePlayVerifyResponse
from app.models.domain import AccountIdentity


class TestGooglePlayVerifyEndpoint:
    """Tests for /v1/billing/google-play/verify endpoint."""

    def test_verify_request_model_valid(self):
        """Test that valid request creates properly."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_123",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        assert request.oauth_provider == "oauth:google"
        assert request.external_id == "user_123"
        assert request.purchase_token == "test_token_12345"
        assert request.product_id == "credits_100"
        assert request.package_name == "ai.ciris.agent"

    def test_verify_request_model_with_all_fields(self):
        """Test request with all optional fields."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_123",
            wa_id="wa_456",
            tenant_id="tenant_789",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            customer_email="test@example.com",
            user_role="observer",
            agent_id="agent_001",
            marketing_opt_in=True,
            marketing_opt_in_source="google_play",
        )

        assert request.wa_id == "wa_456"
        assert request.tenant_id == "tenant_789"
        assert request.customer_email == "test@example.com"
        assert request.user_role == "observer"
        assert request.marketing_opt_in is True

    def test_verify_response_model(self):
        """Test response model creation."""
        response = GooglePlayVerifyResponse(
            verified=True,
            credits_added=100,
            balance_after=100,
            order_id="GPA.1234-5678-9012",
            purchase_time_millis=1700000000000,
            already_processed=False,
        )

        assert response.verified is True
        assert response.credits_added == 100
        assert response.balance_after == 100
        assert response.order_id == "GPA.1234-5678-9012"
        assert response.already_processed is False

    def test_verify_response_already_processed(self):
        """Test response for already processed purchase."""
        response = GooglePlayVerifyResponse(
            verified=True,
            credits_added=100,
            balance_after=200,
            order_id="GPA.1234-5678-9012",
            purchase_time_millis=1700000000000,
            already_processed=True,
        )

        assert response.already_processed is True


class TestGooglePlayWebhookEndpoint:
    """Tests for /v1/billing/webhooks/google-play endpoint."""

    def test_webhook_payload_structure(self):
        """Test expected webhook payload structure."""
        import base64
        import json

        # This is how Google Play sends webhooks via Pub/Sub
        notification = {
            "version": "1.0",
            "packageName": "ai.ciris.agent",
            "eventTimeMillis": "1700000000000",
            "oneTimeProductNotification": {
                "purchaseToken": "test_token_12345",
                "sku": "credits_100",
                "notificationType": 1,  # Purchased
            },
        }

        pubsub_payload = {
            "message": {
                "messageId": "msg_123",
                "data": base64.b64encode(json.dumps(notification).encode()).decode(),
            }
        }

        # Verify structure is correct
        assert "message" in pubsub_payload
        assert "data" in pubsub_payload["message"]

        # Decode and verify
        decoded = json.loads(base64.b64decode(pubsub_payload["message"]["data"]))
        assert decoded["packageName"] == "ai.ciris.agent"
        assert decoded["oneTimeProductNotification"]["sku"] == "credits_100"


class TestRouteImports:
    """Tests that all imports in routes.py work correctly."""

    def test_google_play_imports_available(self):
        """Test that Google Play models can be imported."""
        from app.models.api import GooglePlayVerifyRequest, GooglePlayVerifyResponse
        from app.models.google_play import GooglePlayPurchaseToken, GooglePlayPurchaseVerification
        from app.services.google_play_products import get_credits_for_product

        # All imports should succeed
        assert GooglePlayVerifyRequest is not None
        assert GooglePlayVerifyResponse is not None
        assert GooglePlayPurchaseToken is not None
        assert GooglePlayPurchaseVerification is not None
        assert get_credits_for_product is not None


class TestAccountIdentityCreation:
    """Tests for AccountIdentity creation from request."""

    def test_identity_from_request(self):
        """Test creating AccountIdentity from request fields."""
        request = GooglePlayVerifyRequest(
            oauth_provider="oauth:google",
            external_id="user_123",
            wa_id="wa_456",
            tenant_id="tenant_789",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        identity = AccountIdentity(
            oauth_provider=request.oauth_provider,
            external_id=request.external_id,
            wa_id=request.wa_id,
            tenant_id=request.tenant_id,
        )

        assert identity.oauth_provider == "oauth:google"
        assert identity.external_id == "user_123"
        assert identity.wa_id == "wa_456"
        assert identity.tenant_id == "tenant_789"

    def test_identity_validation(self):
        """Test that AccountIdentity validates oauth_provider."""
        with pytest.raises(ValueError, match="Invalid oauth_provider"):
            AccountIdentity(
                oauth_provider="google",  # Missing 'oauth:' prefix
                external_id="user_123",
                wa_id=None,
                tenant_id=None,
            )
