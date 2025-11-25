"""
Tests for Google Play domain models.
"""

import pytest

from app.models.google_play import (
    GooglePlayPurchaseToken,
    GooglePlayPurchaseVerification,
    GooglePlayWebhookEvent,
)


class TestGooglePlayPurchaseToken:
    """Tests for GooglePlayPurchaseToken validation."""

    def test_valid_token(self):
        """Test creating valid purchase token."""
        token = GooglePlayPurchaseToken(
            token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        assert token.token == "test_token_12345"
        assert token.product_id == "credits_100"
        assert token.package_name == "ai.ciris.agent"

    def test_invalid_token_empty(self):
        """Test that empty token raises ValueError."""
        with pytest.raises(ValueError, match="Invalid purchase token"):
            GooglePlayPurchaseToken(
                token="",
                product_id="credits_100",
                package_name="ai.ciris.agent",
            )

    def test_invalid_token_too_short(self):
        """Test that short token raises ValueError."""
        with pytest.raises(ValueError, match="Invalid purchase token"):
            GooglePlayPurchaseToken(
                token="short",
                product_id="credits_100",
                package_name="ai.ciris.agent",
            )

    def test_missing_product_id(self):
        """Test that missing product ID raises ValueError."""
        with pytest.raises(ValueError, match="Product ID required"):
            GooglePlayPurchaseToken(
                token="test_token_12345",
                product_id="",
                package_name="ai.ciris.agent",
            )

    def test_missing_package_name(self):
        """Test that missing package name raises ValueError."""
        with pytest.raises(ValueError, match="Package name required"):
            GooglePlayPurchaseToken(
                token="test_token_12345",
                product_id="credits_100",
                package_name="",
            )

    def test_immutable(self):
        """Test that GooglePlayPurchaseToken is immutable."""
        token = GooglePlayPurchaseToken(
            token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
        )

        with pytest.raises(AttributeError):
            token.token = "new_token"  # type: ignore[misc]


class TestGooglePlayPurchaseVerification:
    """Tests for GooglePlayPurchaseVerification."""

    def test_valid_purchase(self):
        """Test purchase verification with valid state."""
        verification = GooglePlayPurchaseVerification(
            order_id="GPA.1234-5678-9012",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            purchase_time_millis=1700000000000,
            purchase_state=0,  # Purchased
            acknowledgement_state=0,
            consumption_state=0,
        )

        assert verification.is_valid() is True
        assert verification.needs_acknowledgement() is True
        assert verification.needs_consumption() is True

    def test_canceled_purchase(self):
        """Test purchase verification with canceled state."""
        verification = GooglePlayPurchaseVerification(
            order_id="GPA.1234-5678-9012",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            purchase_time_millis=1700000000000,
            purchase_state=1,  # Canceled
            acknowledgement_state=0,
            consumption_state=0,
        )

        assert verification.is_valid() is False

    def test_acknowledged_purchase(self):
        """Test purchase verification with acknowledgement."""
        verification = GooglePlayPurchaseVerification(
            order_id="GPA.1234-5678-9012",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            purchase_time_millis=1700000000000,
            purchase_state=0,
            acknowledgement_state=1,  # Acknowledged
            consumption_state=0,
        )

        assert verification.is_valid() is True
        assert verification.needs_acknowledgement() is False
        assert verification.needs_consumption() is True

    def test_consumed_purchase(self):
        """Test purchase verification with consumption."""
        verification = GooglePlayPurchaseVerification(
            order_id="GPA.1234-5678-9012",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            purchase_time_millis=1700000000000,
            purchase_state=0,
            acknowledgement_state=1,
            consumption_state=1,  # Consumed
        )

        assert verification.is_valid() is True
        assert verification.needs_acknowledgement() is False
        assert verification.needs_consumption() is False

    def test_immutable(self):
        """Test that GooglePlayPurchaseVerification is immutable."""
        verification = GooglePlayPurchaseVerification(
            order_id="GPA.1234-5678-9012",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            purchase_time_millis=1700000000000,
            purchase_state=0,
            acknowledgement_state=0,
            consumption_state=0,
        )

        with pytest.raises(AttributeError):
            verification.purchase_state = 1  # type: ignore[misc]


class TestGooglePlayWebhookEvent:
    """Tests for GooglePlayWebhookEvent."""

    def test_webhook_event_creation(self):
        """Test creating webhook event."""
        event = GooglePlayWebhookEvent(
            event_id="msg_12345",
            event_type="product_purchased",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            notification_type=1,
            event_time_millis=1700000000000,
        )

        assert event.event_type == "product_purchased"
        assert event.notification_type == 1

    def test_immutable(self):
        """Test that GooglePlayWebhookEvent is immutable."""
        event = GooglePlayWebhookEvent(
            event_id="msg_12345",
            event_type="product_purchased",
            purchase_token="test_token_12345",
            product_id="credits_100",
            package_name="ai.ciris.agent",
            notification_type=1,
            event_time_millis=1700000000000,
        )

        with pytest.raises(AttributeError):
            event.event_type = "product_canceled"  # type: ignore[misc]
