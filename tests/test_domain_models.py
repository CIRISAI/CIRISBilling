"""
Tests for domain models.
"""

import pytest

from app.models.api import TransactionType
from app.models.domain import AccountIdentity, CreditIntent


class TestCreditIntent:
    """Tests for CreditIntent domain model."""

    def test_credit_intent_default_is_test_false(self):
        """Test that is_test defaults to False."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        intent = CreditIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test credit",
            transaction_type=TransactionType.PURCHASE,
            external_transaction_id="order_123",
            idempotency_key="key_123",
        )

        assert intent.is_test is False

    def test_credit_intent_is_test_true(self):
        """Test that is_test can be set to True."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        intent = CreditIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test credit",
            transaction_type=TransactionType.PURCHASE,
            external_transaction_id="order_123",
            idempotency_key="key_123",
            is_test=True,
        )

        assert intent.is_test is True

    def test_credit_intent_is_test_false_explicit(self):
        """Test that is_test can be explicitly set to False."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        intent = CreditIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test credit",
            transaction_type=TransactionType.PURCHASE,
            external_transaction_id="order_123",
            idempotency_key="key_123",
            is_test=False,
        )

        assert intent.is_test is False

    def test_credit_intent_validation_positive_amount(self):
        """Test that amount must be positive."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        with pytest.raises(ValueError, match="Credit amount must be positive"):
            CreditIntent(
                account_identity=identity,
                amount_minor=0,
                currency="USD",
                description="Test credit",
                transaction_type=TransactionType.PURCHASE,
                external_transaction_id="order_123",
                idempotency_key="key_123",
            )

    def test_credit_intent_validation_negative_amount(self):
        """Test that negative amount raises error."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        with pytest.raises(ValueError, match="Credit amount must be positive"):
            CreditIntent(
                account_identity=identity,
                amount_minor=-100,
                currency="USD",
                description="Test credit",
                transaction_type=TransactionType.PURCHASE,
                external_transaction_id="order_123",
                idempotency_key="key_123",
            )

    def test_credit_intent_immutable(self):
        """Test that CreditIntent is immutable."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="12345",
            wa_id=None,
            tenant_id=None,
        )
        intent = CreditIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test credit",
            transaction_type=TransactionType.PURCHASE,
            external_transaction_id="order_123",
            idempotency_key="key_123",
        )

        with pytest.raises(AttributeError):
            intent.is_test = True  # type: ignore[misc]
