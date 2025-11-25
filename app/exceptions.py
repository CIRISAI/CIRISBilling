"""
Exception Classes - Strongly typed exception hierarchy.

NO DICTIONARIES - All exceptions have typed attributes.
"""

from uuid import UUID

from app.models.domain import AccountIdentity


class BillingError(Exception):
    """Base exception for all billing errors."""

    pass


class InsufficientCreditsError(BillingError):
    """Raised when account has insufficient balance for charge."""

    def __init__(self, balance: int, required: int) -> None:
        self.balance = balance
        self.required = required
        super().__init__(f"Insufficient credits. Balance: {balance}, Required: {required}")


class AccountNotFoundError(BillingError):
    """Raised when account doesn't exist."""

    def __init__(self, identity: AccountIdentity) -> None:
        self.identity = identity
        super().__init__(f"Account not found: {identity.oauth_provider}/{identity.external_id}")


class AccountSuspendedError(BillingError):
    """Raised when account is suspended."""

    def __init__(self, account_id: UUID, reason: str) -> None:
        self.account_id = account_id
        self.reason = reason
        super().__init__(f"Account {account_id} suspended: {reason}")


class AccountClosedError(BillingError):
    """Raised when account is closed."""

    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        super().__init__(f"Account {account_id} is closed")


class WriteVerificationError(BillingError):
    """Raised when database write verification fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Write verification failed: {message}")


class DataIntegrityError(BillingError):
    """Raised when data integrity constraint violated."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Data integrity error: {message}")


class IdempotencyConflictError(BillingError):
    """Raised when idempotency key reused with different data."""

    def __init__(self, existing_id: UUID) -> None:
        self.existing_id = existing_id
        super().__init__(f"Idempotency conflict: existing ID {existing_id}")


class DatabaseError(BillingError):
    """Raised when database operation fails unexpectedly."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Database error: {message}")


class ConcurrencyError(BillingError):
    """Raised when concurrent modification detected."""

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"Concurrent modification detected for {resource}")


class PaymentProviderError(BillingError):
    """Raised when payment provider operation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Payment provider error: {message}")


class WebhookVerificationError(BillingError):
    """Raised when webhook verification fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Webhook verification error: {message}")


class AuthenticationError(BillingError):
    """Raised when authentication fails (invalid API key, invalid credentials)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"Authentication failed: {message}")


class AuthorizationError(BillingError):
    """Raised when user lacks required permissions."""

    def __init__(self, required_permission: str) -> None:
        self.required_permission = required_permission
        super().__init__(f"Authorization failed: missing permission {required_permission}")
