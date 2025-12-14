"""
Database Models - SQLAlchemy ORM models with strict typing.

NO DICTIONARIES - All columns use Mapped[] type annotations.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.api import TransactionType


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


def utc_now() -> datetime:
    """Get current UTC timestamp."""
    return datetime.now(UTC)


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

    # Contact information
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Balance
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Plan
    plan_name: Mapped[str] = mapped_column(String(100), nullable=False, default="free")

    # Usage tracking for free tier (one-time signup bonus)
    free_uses_remaining: Mapped[int] = mapped_column(BigInteger, nullable=False, default=3)
    total_uses: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Daily free uses (resets each day)
    daily_free_uses_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    daily_free_uses_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    daily_free_uses_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2)

    # Paid credits (purchased uses)
    paid_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    marketing_opt_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    marketing_opt_in_source: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # User metadata
    user_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
        CheckConstraint("paid_credits >= 0", name="ck_paid_credits_non_negative"),
        CheckConstraint("daily_free_uses_remaining >= 0", name="ck_daily_free_uses_non_negative"),
        CheckConstraint("daily_free_uses_limit > 0", name="ck_daily_free_uses_limit_positive"),
        UniqueConstraint(
            "oauth_provider",
            "external_id",
            "wa_id",
            "tenant_id",
            name="uq_account_identity",
        ),
        Index("idx_accounts_oauth_external", "oauth_provider", "external_id"),
        Index("idx_accounts_wa_id", "wa_id", postgresql_where=(wa_id.isnot(None))),
        Index("idx_accounts_tenant_id", "tenant_id", postgresql_where=(tenant_id.isnot(None))),
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
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)

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
        # Note: balance_after doesn't always equal balance_before - amount_minor
        # When using free tier, balance stays the same but a charge is still recorded
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
    account_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)

    # Amount
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Balance snapshots (denormalized for auditing)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Transaction type
    transaction_type: Mapped[TransactionType] = mapped_column(
        SQLEnum(
            TransactionType,
            name="transaction_type",
            native_enum=False,
            length=20,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # Description
    description: Mapped[str] = mapped_column(String, nullable=False)

    # External reference (Stripe ID, etc.)
    external_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Test purchase flag (for excluding from revenue calculations)
    is_test: Mapped[bool] = mapped_column(nullable=False, default=False)

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
    created_by_id: Mapped[UUID] = mapped_column(
        "created_by",
        PG_UUID(as_uuid=True),
        ForeignKey("admin_users.id"),
        nullable=False,
        index=True,
    )
    created_by: Mapped["AdminUser"] = relationship("AdminUser", lazy="selectin")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Usage tracking
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # Metadata (for rotation tracking, etc.)
    # Note: Database column is "metadata", but Python uses "key_metadata" to avoid SQLAlchemy conflicts
    key_metadata: Mapped[dict[str, str]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        CheckConstraint("environment IN ('test', 'live')", name="ck_api_keys_environment"),
        CheckConstraint("status IN ('active', 'rotating', 'revoked')", name="ck_api_keys_status"),
        Index("idx_api_keys_prefix_active", "key_prefix", postgresql_where=(status == "active")),
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
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'viewer')", name="ck_admin_users_role"),
        CheckConstraint("email LIKE '%@ciris.ai'", name="ck_admin_users_ciris_domain"),
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
    config_data: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    # Audit
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('stripe', 'google_play', 'square', 'paypal')",
            name="ck_provider_configs_type",
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
    changes: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)

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


class GooglePlayPurchase(Base):
    """
    ORM model for google_play_purchases table.

    Tracks Google Play In-App Billing purchases for idempotency.
    """

    __tablename__ = "google_play_purchases"

    # Primary Key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Google Play fields
    purchase_token: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False)
    purchase_time_millis: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchase_state: Mapped[int] = mapped_column(Integer, nullable=False)

    # Processing state
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Credit tracking
    credits_added: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("credits.id", ondelete="SET NULL"), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index("idx_google_play_purchases_purchase_token", "purchase_token", unique=True),
        Index("idx_google_play_purchases_order_id", "order_id"),
        Index("idx_google_play_purchases_account_id", "account_id"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<GooglePlayPurchase(id={self.id}, order_id={self.order_id}, "
            f"product_id={self.product_id}, credits={self.credits_added})>"
        )


class LLMUsageLog(Base):
    """
    ORM model for LLM usage logs.

    Tracks actual provider costs for analytics and margin monitoring.
    Separate from billing - users pay 1 credit per interaction,
    but this tracks what YOU pay to LLM providers.
    """

    __tablename__ = "llm_usage_logs"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Interaction tracking
    interaction_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    charge_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("charges.id", ondelete="SET NULL"), nullable=True
    )

    # Usage metrics
    total_llm_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    total_prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    models_used: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    actual_cost_cents: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Error tracking
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("idx_llm_usage_logs_account_id", "account_id"),
        Index("idx_llm_usage_logs_interaction_id", "interaction_id"),
        Index("idx_llm_usage_logs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<LLMUsageLog(id={self.id}, interaction_id={self.interaction_id}, "
            f"calls={self.total_llm_calls}, cost_cents={self.actual_cost_cents})>"
        )


class ProductInventory(Base):
    """
    ORM model for product_inventory table.

    Tracks tool/product credits per account (e.g., web search, image gen).
    Separate from main LLM credits - each product has its own inventory.
    """

    __tablename__ = "product_inventory"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Product type (e.g., 'web_search', 'image_gen', 'tts')
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Free credits (initial grant + daily refresh)
    free_remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Paid credits (purchased)
    paid_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Daily refresh tracking
    last_daily_refresh: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Usage tracking
    total_uses: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("free_remaining >= 0", name="ck_product_inventory_free_non_negative"),
        CheckConstraint("paid_credits >= 0", name="ck_product_inventory_paid_non_negative"),
        CheckConstraint("total_uses >= 0", name="ck_product_inventory_uses_non_negative"),
        UniqueConstraint("account_id", "product_type", name="uq_product_inventory_account_product"),
        Index("idx_product_inventory_account_id", "account_id"),
        Index("idx_product_inventory_product_type", "product_type"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<ProductInventory(id={self.id}, product={self.product_type}, "
            f"free={self.free_remaining}, paid={self.paid_credits})>"
        )


class ProductUsageLog(Base):
    """
    ORM model for product_usage_logs table.

    Immutable ledger of all tool/product usage for auditing.
    """

    __tablename__ = "product_usage_logs"

    # Primary Key
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Foreign Key to Account
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Product info
    product_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Credit source used
    used_free: Mapped[bool] = mapped_column(Boolean, nullable=False)
    used_paid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cost_minor: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # Actual cost charged (0 if free)

    # Balance snapshots
    free_before: Mapped[int] = mapped_column(Integer, nullable=False)
    free_after: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_before: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_after: Mapped[int] = mapped_column(Integer, nullable=False)

    # Request context
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_product_usage_idempotency"),
        Index("idx_product_usage_logs_account_id", "account_id"),
        Index("idx_product_usage_logs_product_type", "product_type"),
        Index("idx_product_usage_logs_created_at", "created_at"),
        Index(
            "idx_product_usage_logs_idempotency",
            "idempotency_key",
            postgresql_where=(idempotency_key.isnot(None)),
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<ProductUsageLog(id={self.id}, product={self.product_type}, "
            f"cost={self.cost_minor}, used_free={self.used_free})>"
        )


class RevokedToken(Base):
    """
    ORM model for revoked_tokens table.

    Tracks revoked JWT tokens to prevent their use.
    Tokens are identified by a hash of the token (not the token itself).
    Includes TTL for automatic cleanup of expired entries.
    """

    __tablename__ = "revoked_tokens"

    # Primary Key - hash of the token (SHA256)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Token metadata
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)

    # When the token was revoked
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # When the original token expires (for cleanup)
    # After this time, the entry can be safely deleted
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Who revoked it (admin user ID or "system")
    revoked_by: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        Index("idx_revoked_tokens_user_id", "user_id"),
        Index("idx_revoked_tokens_expires_at", "token_expires_at"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<RevokedToken(hash={self.token_hash[:16]}..., "
            f"user_id={self.user_id}, reason={self.reason})>"
        )
