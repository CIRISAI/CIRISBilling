"""
Database Models - SQLAlchemy ORM models with strict typing.

NO DICTIONARIES - All columns use Mapped[] type annotations.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, String, DateTime, Index, UniqueConstraint, Text, Boolean, ARRAY
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.api import AccountStatus, TransactionType


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


def utc_now() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(timezone.utc)


class Account(Base):
    """
    ORM model for accounts table.

    Stores user account information and balances.
    """

    __tablename__ = "accounts"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Identity fields
    oauth_provider: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    wa_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Balance
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Plan
    plan_name: Mapped[str] = mapped_column(String(100), nullable=False, default="free")

    # Usage tracking for free tier
    free_uses_remaining: Mapped[int] = mapped_column(BigInteger, nullable=False, default=3)
    total_uses: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Status
    status: Mapped[AccountStatus] = mapped_column(
        SQLEnum(AccountStatus, name="account_status", native_enum=False, length=20),
        nullable=False,
        default=AccountStatus.ACTIVE,
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    marketing_opt_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    marketing_opt_in_source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Audit timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("balance_minor >= 0", name="ck_balance_non_negative"),
        CheckConstraint("free_uses_remaining >= 0", name="ck_free_uses_non_negative"),
        CheckConstraint("total_uses >= 0", name="ck_total_uses_non_negative"),
        UniqueConstraint(
            "oauth_provider",
            "external_id",
            "wa_id",
            "tenant_id",
            name="uq_account_identity",
        ),
        Index("idx_accounts_oauth_external", "oauth_provider", "external_id"),
        Index("idx_accounts_wa_id", "wa_id", postgresql_where=(wa_id.isnot(None))),
        Index(
            "idx_accounts_tenant_id", "tenant_id", postgresql_where=(tenant_id.isnot(None))
        ),
        Index("idx_accounts_status", "status"),
        Index("idx_accounts_updated_at", "updated_at"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<Account(id={self.id}, oauth_provider={self.oauth_provider}, "
            f"external_id={self.external_id}, balance={self.balance_minor})>"
        )


class Charge(Base):
    """
    ORM model for charges table.

    Immutable ledger of all credit deductions.
    """

    __tablename__ = "charges"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )

    # Amount
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Balance snapshots (denormalized for auditing)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Description
    description: Mapped[str] = mapped_column(String, nullable=False)

    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Metadata fields (no JSON - explicit columns)
    metadata_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_charge_amount_positive"),
        CheckConstraint(
            "balance_after = balance_before - amount_minor",
            name="ck_charge_balance_consistency",
        ),
        UniqueConstraint("account_id", "idempotency_key", name="uq_charge_idempotency"),
        Index("idx_charges_created_at", "created_at"),
        Index(
            "idx_charges_idempotency_key",
            "idempotency_key",
            postgresql_where=(idempotency_key.isnot(None)),
        ),
        Index(
            "idx_charges_request_id",
            "metadata_request_id",
            postgresql_where=(metadata_request_id.isnot(None)),
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<Charge(id={self.id}, account_id={self.account_id}, "
            f"amount={self.amount_minor}, balance_after={self.balance_after})>"
        )


class Credit(Base):
    """
    ORM model for credits table.

    Immutable ledger of all credit additions (purchases, grants, refunds).
    """

    __tablename__ = "credits"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )

    # Amount
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Balance snapshots (denormalized for auditing)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Transaction type
    transaction_type: Mapped[TransactionType] = mapped_column(
        SQLEnum(TransactionType, name="transaction_type", native_enum=False, length=20),
        nullable=False,
    )

    # Description
    description: Mapped[str] = mapped_column(String, nullable=False)

    # External reference (Stripe ID, etc.)
    external_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_credit_amount_positive"),
        CheckConstraint(
            "balance_after = balance_before + amount_minor",
            name="ck_credit_balance_consistency",
        ),
        UniqueConstraint("account_id", "idempotency_key", name="uq_credit_idempotency"),
        Index("idx_credits_created_at", "created_at"),
        Index("idx_credits_transaction_type", "transaction_type"),
        Index(
            "idx_credits_external_transaction_id",
            "external_transaction_id",
            postgresql_where=(external_transaction_id.isnot(None)),
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<Credit(id={self.id}, account_id={self.account_id}, "
            f"amount={self.amount_minor}, type={self.transaction_type})>"
        )


class CreditCheck(Base):
    """
    ORM model for credit_checks table.

    Audit log of all credit check requests for analytics and fraud detection.
    """

    __tablename__ = "credit_checks"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key (nullable - might check non-existent account)
    account_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # Request details
    oauth_provider: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    wa_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Result
    has_credit: Mapped[bool] = mapped_column(nullable=False)
    credits_remaining: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    denial_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # Context
    context_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index(
            "idx_credit_checks_account_id",
            "account_id",
            postgresql_where=(account_id.isnot(None)),
        ),
        Index("idx_credit_checks_created_at", "created_at"),
        Index("idx_credit_checks_oauth_external", "oauth_provider", "external_id"),
        Index("idx_credit_checks_has_credit", "has_credit"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<CreditCheck(id={self.id}, has_credit={self.has_credit}, "
            f"oauth_provider={self.oauth_provider})>"
        )


class APIKey(Base):
    """
    ORM model for api_keys table.

    Stores hashed API keys for agent authentication.
    """

    __tablename__ = "api_keys"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Key storage (hashed with Argon2id)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)

    # Metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment: Mapped[str] = mapped_column(String(10), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=["billing:read", "billing:write"]
    )

    # Ownership
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Usage tracking
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )

    __table_args__ = (
        CheckConstraint(
            "environment IN ('test', 'live')",
            name="ck_api_keys_environment"
        ),
        CheckConstraint(
            "status IN ('active', 'rotating', 'revoked')",
            name="ck_api_keys_status"
        ),
        Index(
            "idx_api_keys_prefix_active",
            "key_prefix",
            postgresql_where=(status == "active")
        ),
        Index("idx_api_keys_created_by", "created_by"),
        Index("idx_api_keys_status", "status"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<APIKey(id={self.id}, name={self.name}, "
            f"prefix={self.key_prefix}, status={self.status})>"
        )


class AdminUser(Base):
    """
    ORM model for admin_users table.

    Admin accounts for accessing the billing admin UI via Google OAuth.
    """

    __tablename__ = "admin_users"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # OAuth Identity
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    google_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    # Profile
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    picture_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Role (simplified to 2 roles)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'viewer')",
            name="ck_admin_users_role"
        ),
        CheckConstraint(
            "email LIKE '%@ciris.ai'",
            name="ck_admin_users_ciris_domain"
        ),
        Index("idx_admin_users_email", "email"),
        Index("idx_admin_users_google_id", "google_id"),
        Index("idx_admin_users_role", "role"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<AdminUser(id={self.id}, email={self.email}, "
            f"role={self.role}, active={self.is_active})>"
        )


class ProviderConfig(Base):
    """
    ORM model for provider_configs table.

    Configuration for payment providers (Stripe, Square, etc.).
    """

    __tablename__ = "provider_configs"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Provider
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Configuration data (encrypted secrets)
    config_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    # Audit
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('stripe', 'square', 'paypal')",
            name="ck_provider_configs_type"
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<ProviderConfig(id={self.id}, type={self.provider_type}, "
            f"active={self.is_active})>"
        )


class AdminAuditLog(Base):
    """
    ORM model for admin_audit_logs table.

    Immutable audit trail of all admin actions.
    """

    __tablename__ = "admin_audit_logs"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Admin user (nullable for system actions)
    admin_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )

    # Action details
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Changes (JSON diff)
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Request context
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("idx_admin_audit_logs_admin_user", "admin_user_id"),
        Index("idx_admin_audit_logs_created_at", "created_at", postgresql_using="brin"),
        Index("idx_admin_audit_logs_action", "action"),
        Index("idx_admin_audit_logs_resource", "resource_type", "resource_id"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<AdminAuditLog(id={self.id}, action={self.action}, "
            f"resource={self.resource_type})>"
        )
