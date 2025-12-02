"""
API Models - Pydantic models for request/response validation.

NO DICTIONARIES - All data structures are strongly typed.
"""

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AccountStatus(str, Enum):
    """Account status enumeration."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class TransactionType(str, Enum):
    """Credit transaction type enumeration."""

    PURCHASE = "purchase"
    GRANT = "grant"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


# ============================================================================
# Credit Check Models
# ============================================================================


class CreditCheckContext(BaseModel):
    """Context for credit check request - explicit fields, no dict."""

    agent_id: str | None = None
    channel_id: str | None = None
    request_id: str | None = None


class CreditCheckRequest(BaseModel):
    """POST /v1/billing/credits/check request body."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    context: CreditCheckContext = Field(default_factory=CreditCheckContext)

    # User contact information
    customer_email: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User email address for receipts and notifications",
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User display name",
    )

    # User metadata
    user_role: str | None = Field(
        None, max_length=50, description="User role (admin, authority, observer)"
    )
    agent_id: str | None = Field(
        None, max_length=255, description="Unique agent ID making the request"
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: bool = Field(
        default=False, description="User consent for marketing communications"
    )
    marketing_opt_in_source: str | None = Field(
        None, max_length=50, description="Source of consent (e.g., 'oauth_login', 'settings')"
    )

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v


class CreditCheckResponse(BaseModel):
    """POST /v1/billing/credits/check response."""

    has_credit: bool
    credits_remaining: int = 0
    plan_name: str | None = None
    reason: str | None = None
    free_uses_remaining: int = 0
    total_uses: int = 0
    purchase_required: bool = False
    purchase_price_minor: int = 0
    purchase_uses: int = 0
    # Daily free uses (resets each day)
    daily_free_uses_remaining: int = 0
    daily_free_uses_limit: int = 0


# ============================================================================
# Charge Models
# ============================================================================


class ChargeMetadata(BaseModel):
    """Metadata for charge - explicit fields, no dict."""

    message_id: str | None = None
    agent_id: str | None = None
    channel_id: str | None = None
    request_id: str | None = None


class CreateChargeRequest(BaseModel):
    """POST /v1/billing/charges request body."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    amount_minor: int = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    description: str = Field(..., min_length=1)
    idempotency_key: str | None = Field(None, max_length=255)
    metadata: ChargeMetadata = Field(default_factory=ChargeMetadata)

    # User contact information
    customer_email: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User email address for receipts and notifications",
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User display name",
    )

    # User metadata
    user_role: str | None = Field(
        None, max_length=50, description="User role (admin, authority, observer)"
    )
    agent_id: str | None = Field(
        None, max_length=255, description="Unique agent ID making the request"
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: bool = Field(
        default=False, description="User consent for marketing communications"
    )
    marketing_opt_in_source: str | None = Field(
        None, max_length=50, description="Source of consent (e.g., 'oauth_login', 'settings')"
    )

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Ensure currency is uppercase ISO 4217 code."""
        return v.upper()


class ChargeResponse(BaseModel):
    """POST /v1/billing/charges response."""

    charge_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    balance_after: int
    created_at: str  # ISO 8601 timestamp
    description: str
    metadata: ChargeMetadata


class ChargeListItem(BaseModel):
    """Single charge in list response."""

    charge_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    balance_after: int
    created_at: str
    description: str
    metadata: ChargeMetadata


class ListChargesResponse(BaseModel):
    """GET /v1/billing/charges response."""

    charges: list[ChargeListItem]
    total_count: int
    has_more: bool


# ============================================================================
# Credit (Top-up) Models
# ============================================================================


class AddCreditsRequest(BaseModel):
    """POST /v1/billing/credits request body."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    amount_minor: int = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    description: str = Field(..., min_length=1)
    transaction_type: TransactionType
    external_transaction_id: str | None = Field(None, max_length=255)
    idempotency_key: str | None = Field(None, max_length=255)

    # User contact information
    customer_email: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User email address for receipts and notifications",
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User display name",
    )

    # User metadata
    user_role: str | None = Field(
        None, max_length=50, description="User role (admin, authority, observer)"
    )
    agent_id: str | None = Field(
        None, max_length=255, description="Unique agent ID making the request"
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: bool = Field(
        default=False, description="User consent for marketing communications"
    )
    marketing_opt_in_source: str | None = Field(
        None, max_length=50, description="Source of consent (e.g., 'oauth_login', 'settings')"
    )

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Ensure currency is uppercase ISO 4217 code."""
        return v.upper()


class CreditResponse(BaseModel):
    """POST /v1/billing/credits response."""

    credit_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    balance_after: int
    transaction_type: TransactionType
    description: str
    external_transaction_id: str | None
    created_at: str


# ============================================================================
# Purchase Models
# ============================================================================


class PurchaseRequest(BaseModel):
    """POST /v1/billing/purchases request body."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    customer_email: str = Field(..., min_length=1)
    return_url: str | None = Field(None, min_length=1)

    # User metadata
    user_role: str | None = Field(
        None, max_length=50, description="User role (admin, authority, observer)"
    )
    agent_id: str | None = Field(
        None, max_length=255, description="Unique agent ID making the request"
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: bool = Field(
        default=False, description="User consent for marketing communications"
    )
    marketing_opt_in_source: str | None = Field(
        None, max_length=50, description="Source of consent (e.g., 'oauth_login', 'settings')"
    )

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v


class PurchaseResponse(BaseModel):
    """POST /v1/billing/purchases response."""

    payment_id: str
    client_secret: str
    amount_minor: int
    currency: str
    uses_purchased: int
    status: str
    publishable_key: str


# ============================================================================
# Transaction Models (Unified Charges + Credits)
# ============================================================================


class TransactionItem(BaseModel):
    """Single transaction in unified transaction list (charge or credit)."""

    transaction_id: UUID
    type: Literal["charge", "credit"]
    amount_minor: int  # Negative for charges, positive for credits
    currency: str
    description: str
    created_at: str  # ISO 8601 timestamp
    balance_after: int

    # Optional fields that may appear on credits
    transaction_type: TransactionType | None = (
        None  # For credits: purchase, grant, refund, adjustment
    )
    external_transaction_id: str | None = None  # For credits: Stripe payment ID, etc.

    # Optional fields that may appear on charges
    metadata: ChargeMetadata | None = None


class TransactionListRequest(BaseModel):
    """Query parameters for listing transactions."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v


class TransactionListResponse(BaseModel):
    """GET /v1/billing/transactions response."""

    transactions: list[TransactionItem]
    total_count: int
    has_more: bool


# ============================================================================
# Account Models
# ============================================================================


class CreateAccountRequest(BaseModel):
    """POST /v1/billing/accounts request body."""

    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)
    initial_balance_minor: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    plan_name: str = Field(default="free", min_length=1, max_length=100)

    # User contact information
    customer_email: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User email address for receipts and notifications",
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="User display name",
    )

    # User metadata
    user_role: str | None = Field(
        None, max_length=50, description="User role (admin, authority, observer)"
    )
    agent_id: str | None = Field(
        None, max_length=255, description="Unique agent ID making the request"
    )

    # Marketing consent (GDPR compliance)
    marketing_opt_in: bool = Field(
        default=False, description="User consent for marketing communications"
    )
    marketing_opt_in_source: str | None = Field(
        None, max_length=50, description="Source of consent (e.g., 'oauth_login', 'settings')"
    )

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Ensure currency is uppercase ISO 4217 code."""
        return v.upper()


class AccountResponse(BaseModel):
    """Account response model (GET and POST)."""

    account_id: UUID
    oauth_provider: str
    external_id: str
    wa_id: str | None
    tenant_id: str | None
    customer_email: str | None
    balance_minor: int
    currency: str
    plan_name: str
    status: AccountStatus
    paid_credits: int
    marketing_opt_in: bool
    marketing_opt_in_at: str | None
    marketing_opt_in_source: str | None
    created_at: str
    updated_at: str


# ============================================================================
# Health Check Models
# ============================================================================


class HealthResponse(BaseModel):
    """GET /health response."""

    status: Literal["healthy", "unhealthy"]
    database: Literal["connected", "disconnected"]
    timestamp: str


# ============================================================================
# Error Models
# ============================================================================


class ErrorDetail(BaseModel):
    """Standard error response detail."""

    detail: str


class ValidationErrorDetail(BaseModel):
    """Validation error location."""

    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    """422 Validation Error response."""

    detail: list[ValidationErrorDetail]


# ============================================================================
# Google Play Models
# ============================================================================


class GooglePlayVerifyRequest(BaseModel):
    """POST /v1/billing/google-play/verify request body."""

    # Account identity (same pattern as other endpoints)
    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)
    wa_id: str | None = Field(None, max_length=255)
    tenant_id: str | None = Field(None, max_length=255)

    # Google Play purchase details
    purchase_token: str = Field(..., min_length=10, max_length=4096)
    product_id: str = Field(..., max_length=255)
    package_name: str = Field(..., max_length=255)

    # User contact information
    customer_email: str | None = Field(None, min_length=1, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=255)

    # User metadata
    user_role: str | None = Field(None, max_length=50)
    agent_id: str | None = Field(None, max_length=255)

    # Marketing consent
    marketing_opt_in: bool = Field(default=False)
    marketing_opt_in_source: str | None = Field(None, max_length=50)

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v

    @field_validator("purchase_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate purchase token."""
        if not v or len(v.strip()) < 10:
            raise ValueError("Invalid purchase token")
        return v.strip()


class GooglePlayVerifyResponse(BaseModel):
    """POST /v1/billing/google-play/verify response."""

    verified: bool
    credits_added: int
    balance_after: int
    order_id: str | None = None
    purchase_time_millis: int | None = None
    already_processed: bool = False


# ============================================================================
# LiteLLM Proxy Integration Models
# ============================================================================


class LiteLLMAuthRequest(BaseModel):
    """
    LiteLLM pre-request auth check.

    Called by LiteLLM proxy before allowing a request.
    Simple check: does user have at least 1 credit?
    """

    # User identity (from auth token)
    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)

    # Request context (optional)
    model: str | None = Field(None, max_length=255, description="Requested model name")
    interaction_id: str | None = Field(None, max_length=255, description="Unique interaction ID")

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v


class LiteLLMAuthResponse(BaseModel):
    """LiteLLM pre-request auth response."""

    authorized: bool = Field(..., description="Whether user is authorized for request")
    credits_remaining: int = Field(..., description="Credits remaining after this interaction")
    reason: str | None = Field(None, description="Reason if not authorized")
    interaction_id: str | None = Field(None, description="Interaction ID for tracking")


class LiteLLMChargeRequest(BaseModel):
    """
    LiteLLM post-interaction charge.

    Called by LiteLLM proxy after successful interaction completes.
    Deducts 1 credit per interaction (regardless of actual LLM calls).
    """

    # User identity
    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)

    # Interaction tracking
    interaction_id: str = Field(..., min_length=1, max_length=255)

    # Idempotency (prevent double-charging)
    idempotency_key: str | None = Field(None, max_length=255)

    @field_validator("oauth_provider")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if not v.startswith("oauth:"):
            raise ValueError('oauth_provider must start with "oauth:"')
        return v


class LiteLLMChargeResponse(BaseModel):
    """LiteLLM post-interaction charge response."""

    charged: bool = Field(..., description="Whether charge was successful")
    credits_deducted: int = Field(default=1, description="Credits deducted (always 1)")
    credits_remaining: int = Field(..., description="Credits remaining after charge")
    charge_id: UUID | None = Field(None, description="Charge record ID")


class LiteLLMUsageLogRequest(BaseModel):
    """
    LiteLLM usage analytics logging.

    Called by LiteLLM proxy to log actual costs for YOUR analytics.
    This is separate from billing - users pay 1 credit per interaction,
    but you want to track actual provider costs to monitor margins.
    """

    # User identity
    oauth_provider: str = Field(..., min_length=1, max_length=255)
    external_id: str = Field(..., min_length=1, max_length=255)

    # Interaction reference
    interaction_id: str = Field(..., min_length=1, max_length=255)

    # Usage metrics - made more flexible (ge=0 instead of ge=1 for calls)
    total_llm_calls: int = Field(
        ..., ge=0, description="Number of LLM API calls in this interaction"
    )
    total_prompt_tokens: int = Field(..., ge=0, description="Total prompt tokens across all calls")
    total_completion_tokens: int = Field(..., ge=0, description="Total completion tokens")
    models_used: list[str] = Field(default_factory=list, description="List of models used")
    actual_cost_cents: int = Field(..., ge=0, description="Actual cost to providers in cents")
    duration_ms: int = Field(..., ge=0, description="Total interaction duration in milliseconds")

    # Error tracking
    error_count: int = Field(default=0, ge=0, description="Number of failed LLM calls")
    fallback_count: int = Field(default=0, ge=0, description="Number of fallback triggers")

    @field_validator("oauth_provider", mode="before")
    @classmethod
    def validate_oauth_provider(cls, v: str) -> str:
        """Ensure oauth_provider follows oauth: prefix convention."""
        if isinstance(v, str) and not v.startswith("oauth:"):
            # Auto-fix by prepending oauth: if missing
            return f"oauth:{v}"
        return v


class LiteLLMUsageLogResponse(BaseModel):
    """LiteLLM usage log response."""

    logged: bool = Field(..., description="Whether usage was logged")
    usage_log_id: UUID | None = Field(None, description="Usage log record ID")


# ============================================================================
# User-Facing Models (JWT/Google ID Token auth)
# ============================================================================


class UserBalanceResponse(BaseModel):
    """
    GET /v1/user/balance response.

    Returns the user's current credit balance and account info.
    Used by Android app to display credits to user.
    """

    # Matches what Android BillingApiClient expects
    success: bool = Field(..., description="Whether the request succeeded")
    balance: int = Field(..., description="Total available credits (free + paid + daily)")
    paid_credits: int = Field(0, description="Purchased credits remaining")
    free_credits: int = Field(0, description="One-time signup bonus credits remaining")
    daily_free_uses_remaining: int = Field(0, description="Daily free uses left today")
    daily_free_uses_limit: int = Field(2, description="Maximum daily free uses")
    email: str | None = Field(None, description="User's email address")


class UserGooglePlayVerifyRequest(BaseModel):
    """
    POST /v1/user/google-play/verify request body.

    User-facing version of GooglePlayVerifyRequest - identity comes from JWT token,
    not from request body.
    """

    purchase_token: str = Field(..., min_length=10, max_length=4096)
    product_id: str = Field(..., max_length=255)
    package_name: str = Field(..., max_length=255)

    @field_validator("purchase_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate purchase token."""
        if not v or len(v.strip()) < 10:
            raise ValueError("Invalid purchase token")
        return v.strip()


class UserGooglePlayVerifyResponse(BaseModel):
    """
    POST /v1/user/google-play/verify response.

    Matches what Android BillingApiClient.VerifyResponse expects:
    - success (not verified)
    - credits_added
    - new_balance (not balance_after)
    - already_processed
    """

    success: bool = Field(..., description="Whether verification succeeded")
    credits_added: int = Field(0, description="Credits added from this purchase")
    new_balance: int = Field(0, description="New total balance after purchase")
    already_processed: bool = Field(False, description="Whether purchase was already processed")
