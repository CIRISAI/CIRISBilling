"""
Tests for exception classes.

Covers all exception types and their string representations.
"""

from uuid import uuid4

import pytest

from app.exceptions import (
    AccountClosedError,
    AccountNotFoundError,
    AccountSuspendedError,
    AuthenticationError,
    AuthorizationError,
    BillingError,
    ConcurrencyError,
    DatabaseError,
    DataIntegrityError,
    IdempotencyConflictError,
    InsufficientCreditsError,
    PaymentProviderError,
    ResourceNotFoundError,
    WebhookVerificationError,
    WriteVerificationError,
)
from app.models.domain import AccountIdentity


class TestBillingError:
    """Tests for base BillingError."""

    def test_billing_error_is_exception(self):
        """BillingError is a subclass of Exception."""
        assert issubclass(BillingError, Exception)

    def test_billing_error_can_be_raised(self):
        """BillingError can be raised and caught."""
        with pytest.raises(BillingError):
            raise BillingError("test error")


class TestInsufficientCreditsError:
    """Tests for InsufficientCreditsError."""

    def test_attributes(self):
        """Exception has balance and required attributes."""
        exc = InsufficientCreditsError(balance=50, required=100)
        assert exc.balance == 50
        assert exc.required == 100

    def test_message_format(self):
        """Exception message includes balance and required."""
        exc = InsufficientCreditsError(balance=50, required=100)
        assert "50" in str(exc)
        assert "100" in str(exc)
        assert "Insufficient credits" in str(exc)

    def test_is_billing_error(self):
        """InsufficientCreditsError is a BillingError."""
        exc = InsufficientCreditsError(balance=0, required=1)
        assert isinstance(exc, BillingError)


class TestAccountNotFoundError:
    """Tests for AccountNotFoundError."""

    def test_attributes(self):
        """Exception has identity attribute."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            wa_id=None,
            tenant_id=None,
        )
        exc = AccountNotFoundError(identity)
        assert exc.identity == identity

    def test_message_format(self):
        """Exception message includes oauth_provider and external_id."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            wa_id=None,
            tenant_id=None,
        )
        exc = AccountNotFoundError(identity)
        assert "oauth:google" in str(exc)
        assert "test@example.com" in str(exc)
        assert "Account not found" in str(exc)

    def test_is_billing_error(self):
        """AccountNotFoundError is a BillingError."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            wa_id=None,
            tenant_id=None,
        )
        exc = AccountNotFoundError(identity)
        assert isinstance(exc, BillingError)


class TestAccountSuspendedError:
    """Tests for AccountSuspendedError."""

    def test_attributes(self):
        """Exception has account_id and reason attributes."""
        account_id = uuid4()
        exc = AccountSuspendedError(account_id, "Payment failed")
        assert exc.account_id == account_id
        assert exc.reason == "Payment failed"

    def test_message_format(self):
        """Exception message includes account_id and reason."""
        account_id = uuid4()
        exc = AccountSuspendedError(account_id, "Payment failed")
        assert str(account_id) in str(exc)
        assert "Payment failed" in str(exc)
        assert "suspended" in str(exc)

    def test_is_billing_error(self):
        """AccountSuspendedError is a BillingError."""
        exc = AccountSuspendedError(uuid4(), "test")
        assert isinstance(exc, BillingError)


class TestAccountClosedError:
    """Tests for AccountClosedError."""

    def test_attributes(self):
        """Exception has account_id attribute."""
        account_id = uuid4()
        exc = AccountClosedError(account_id)
        assert exc.account_id == account_id

    def test_message_format(self):
        """Exception message includes account_id."""
        account_id = uuid4()
        exc = AccountClosedError(account_id)
        assert str(account_id) in str(exc)
        assert "closed" in str(exc)

    def test_is_billing_error(self):
        """AccountClosedError is a BillingError."""
        exc = AccountClosedError(uuid4())
        assert isinstance(exc, BillingError)


class TestWriteVerificationError:
    """Tests for WriteVerificationError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = WriteVerificationError("Record not found after insert")
        assert exc.message == "Record not found after insert"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = WriteVerificationError("Record not found after insert")
        assert "Record not found after insert" in str(exc)
        assert "Write verification failed" in str(exc)

    def test_is_billing_error(self):
        """WriteVerificationError is a BillingError."""
        exc = WriteVerificationError("test")
        assert isinstance(exc, BillingError)


class TestDataIntegrityError:
    """Tests for DataIntegrityError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = DataIntegrityError("Currency mismatch")
        assert exc.message == "Currency mismatch"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = DataIntegrityError("Currency mismatch")
        assert "Currency mismatch" in str(exc)
        assert "Data integrity error" in str(exc)

    def test_is_billing_error(self):
        """DataIntegrityError is a BillingError."""
        exc = DataIntegrityError("test")
        assert isinstance(exc, BillingError)


class TestIdempotencyConflictError:
    """Tests for IdempotencyConflictError."""

    def test_attributes(self):
        """Exception has existing_id attribute."""
        existing_id = uuid4()
        exc = IdempotencyConflictError(existing_id)
        assert exc.existing_id == existing_id

    def test_message_format(self):
        """Exception message includes existing_id."""
        existing_id = uuid4()
        exc = IdempotencyConflictError(existing_id)
        assert str(existing_id) in str(exc)
        assert "Idempotency conflict" in str(exc)

    def test_is_billing_error(self):
        """IdempotencyConflictError is a BillingError."""
        exc = IdempotencyConflictError(uuid4())
        assert isinstance(exc, BillingError)


class TestDatabaseError:
    """Tests for DatabaseError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = DatabaseError("Connection timeout")
        assert exc.message == "Connection timeout"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = DatabaseError("Connection timeout")
        assert "Connection timeout" in str(exc)
        assert "Database error" in str(exc)

    def test_is_billing_error(self):
        """DatabaseError is a BillingError."""
        exc = DatabaseError("test")
        assert isinstance(exc, BillingError)


class TestConcurrencyError:
    """Tests for ConcurrencyError."""

    def test_attributes(self):
        """Exception has resource attribute."""
        exc = ConcurrencyError("account:123")
        assert exc.resource == "account:123"

    def test_message_format(self):
        """Exception message includes resource."""
        exc = ConcurrencyError("account:123")
        assert "account:123" in str(exc)
        assert "Concurrent modification" in str(exc)

    def test_is_billing_error(self):
        """ConcurrencyError is a BillingError."""
        exc = ConcurrencyError("test")
        assert isinstance(exc, BillingError)


class TestPaymentProviderError:
    """Tests for PaymentProviderError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = PaymentProviderError("Stripe API unavailable")
        assert exc.message == "Stripe API unavailable"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = PaymentProviderError("Stripe API unavailable")
        assert "Stripe API unavailable" in str(exc)
        assert "Payment provider error" in str(exc)

    def test_is_billing_error(self):
        """PaymentProviderError is a BillingError."""
        exc = PaymentProviderError("test")
        assert isinstance(exc, BillingError)


class TestWebhookVerificationError:
    """Tests for WebhookVerificationError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = WebhookVerificationError("Invalid signature")
        assert exc.message == "Invalid signature"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = WebhookVerificationError("Invalid signature")
        assert "Invalid signature" in str(exc)
        assert "Webhook verification error" in str(exc)

    def test_is_billing_error(self):
        """WebhookVerificationError is a BillingError."""
        exc = WebhookVerificationError("test")
        assert isinstance(exc, BillingError)


class TestAuthenticationError:
    """Tests for AuthenticationError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = AuthenticationError("Invalid API key")
        assert exc.message == "Invalid API key"

    def test_message_format(self):
        """Exception message includes the provided message."""
        exc = AuthenticationError("Invalid API key")
        assert "Invalid API key" in str(exc)
        assert "Authentication failed" in str(exc)

    def test_is_billing_error(self):
        """AuthenticationError is a BillingError."""
        exc = AuthenticationError("test")
        assert isinstance(exc, BillingError)


class TestAuthorizationError:
    """Tests for AuthorizationError."""

    def test_attributes(self):
        """Exception has required_permission attribute."""
        exc = AuthorizationError("billing:write")
        assert exc.required_permission == "billing:write"

    def test_message_format(self):
        """Exception message includes required permission."""
        exc = AuthorizationError("billing:write")
        assert "billing:write" in str(exc)
        assert "Authorization failed" in str(exc)

    def test_is_billing_error(self):
        """AuthorizationError is a BillingError."""
        exc = AuthorizationError("test")
        assert isinstance(exc, BillingError)


class TestResourceNotFoundError:
    """Tests for ResourceNotFoundError."""

    def test_attributes(self):
        """Exception has message attribute."""
        exc = ResourceNotFoundError("API key not found")
        assert exc.message == "API key not found"

    def test_message_format(self):
        """Exception message is the provided message."""
        exc = ResourceNotFoundError("API key not found")
        assert str(exc) == "API key not found"

    def test_is_billing_error(self):
        """ResourceNotFoundError is a BillingError."""
        exc = ResourceNotFoundError("test")
        assert isinstance(exc, BillingError)


class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_all_exceptions_are_billing_errors(self):
        """All custom exceptions inherit from BillingError."""
        exception_classes = [
            InsufficientCreditsError,
            AccountNotFoundError,
            AccountSuspendedError,
            AccountClosedError,
            WriteVerificationError,
            DataIntegrityError,
            IdempotencyConflictError,
            DatabaseError,
            ConcurrencyError,
            PaymentProviderError,
            WebhookVerificationError,
            AuthenticationError,
            AuthorizationError,
            ResourceNotFoundError,
        ]
        for exc_class in exception_classes:
            assert issubclass(exc_class, BillingError)

    def test_exceptions_can_be_caught_as_billing_error(self):
        """All exceptions can be caught with except BillingError."""
        identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="test",
            wa_id=None,
            tenant_id=None,
        )
        exceptions = [
            InsufficientCreditsError(0, 1),
            AccountNotFoundError(identity),
            AccountSuspendedError(uuid4(), "test"),
            AccountClosedError(uuid4()),
            WriteVerificationError("test"),
            DataIntegrityError("test"),
            IdempotencyConflictError(uuid4()),
            DatabaseError("test"),
            ConcurrencyError("test"),
            PaymentProviderError("test"),
            WebhookVerificationError("test"),
            AuthenticationError("test"),
            AuthorizationError("test"),
            ResourceNotFoundError("test"),
        ]

        for exc in exceptions:
            try:
                raise exc
            except BillingError:
                pass  # Expected
            except Exception:
                pytest.fail(f"{type(exc).__name__} was not caught as BillingError")
