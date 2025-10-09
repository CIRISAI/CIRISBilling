"""
Domain Models - Internal business logic models using dataclasses.

NO DICTIONARIES - All data structures are strongly typed immutable dataclasses.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models.api import AccountStatus, ChargeMetadata, TransactionType


@dataclass(frozen=True)
class AccountIdentity:
    """Immutable account identity - replaces dict-based identity."""

    oauth_provider: str
    external_id: str
    wa_id: str | None
    tenant_id: str | None

    def __post_init__(self) -> None:
        """Validate account identity fields."""
        if not self.oauth_provider.startswith("oauth:"):
            raise ValueError(f"Invalid oauth_provider: {self.oauth_provider}")
        if not self.external_id:
            raise ValueError("external_id cannot be empty")


@dataclass(frozen=True)
class BalanceSnapshot:
    """Immutable balance state at a point in time."""

    balance_minor: int
    currency: str
    timestamp: datetime

    def __post_init__(self) -> None:
        """Validate balance constraints."""
        if self.balance_minor < 0:
            raise ValueError(f"Balance cannot be negative: {self.balance_minor}")
        if len(self.currency) != 3:
            raise ValueError(f"Invalid currency code: {self.currency}")


@dataclass(frozen=True)
class ChargeIntent:
    """Domain model for charge before persistence - immutable intent."""

    account_identity: AccountIdentity
    amount_minor: int
    currency: str
    description: str
    metadata: ChargeMetadata
    idempotency_key: str | None

    def __post_init__(self) -> None:
        """Validate charge constraints."""
        if self.amount_minor <= 0:
            raise ValueError(f"Charge amount must be positive: {self.amount_minor}")
        if not self.description:
            raise ValueError("Description cannot be empty")
        if len(self.currency) != 3:
            raise ValueError(f"Invalid currency code: {self.currency}")


@dataclass(frozen=True)
class CreditIntent:
    """Domain model for credit addition before persistence - immutable intent."""

    account_identity: AccountIdentity
    amount_minor: int
    currency: str
    description: str
    transaction_type: TransactionType
    external_transaction_id: str | None
    idempotency_key: str | None

    def __post_init__(self) -> None:
        """Validate credit constraints."""
        if self.amount_minor <= 0:
            raise ValueError(f"Credit amount must be positive: {self.amount_minor}")
        if not self.description:
            raise ValueError("Description cannot be empty")
        if len(self.currency) != 3:
            raise ValueError(f"Invalid currency code: {self.currency}")


@dataclass(frozen=True)
class AccountData:
    """Immutable account data snapshot."""

    account_id: UUID
    oauth_provider: str
    external_id: str
    wa_id: str | None
    tenant_id: str | None
    balance_minor: int
    currency: str
    plan_name: str
    status: AccountStatus
    marketing_opt_in: bool
    marketing_opt_in_at: datetime | None
    marketing_opt_in_source: str | None
    created_at: datetime
    updated_at: datetime

    def to_identity(self) -> AccountIdentity:
        """Convert to AccountIdentity."""
        return AccountIdentity(
            oauth_provider=self.oauth_provider,
            external_id=self.external_id,
            wa_id=self.wa_id,
            tenant_id=self.tenant_id,
        )


@dataclass(frozen=True)
class ChargeData:
    """Immutable charge data after persistence."""

    charge_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    balance_before: int
    balance_after: int
    description: str
    metadata: ChargeMetadata
    created_at: datetime


@dataclass(frozen=True)
class CreditData:
    """Immutable credit data after persistence."""

    credit_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    balance_before: int
    balance_after: int
    transaction_type: TransactionType
    description: str
    external_transaction_id: str | None
    created_at: datetime


# ============================================================================
# OAuth Models (for admin authentication)
# ============================================================================


@dataclass(frozen=True)
class OAuthToken:
    """OAuth token data."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None


@dataclass(frozen=True)
class OAuthUser:
    """OAuth user information."""

    id: str
    email: str
    name: str | None = None
    picture: str | None = None

    def __post_init__(self) -> None:
        """Validate user is from @ciris.ai domain."""
        if not self.email.endswith("@ciris.ai"):
            raise ValueError(f"Only @ciris.ai emails allowed. Got: {self.email}")


@dataclass(frozen=True)
class OAuthSession:
    """OAuth session data."""

    redirect_uri: str
    callback_url: str
    created_at: str
