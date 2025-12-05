"""
Play Integrity API Models.

Models for Google Play Integrity verification requests and responses.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class DeviceRecognitionVerdict(str, Enum):
    """Device integrity verdict levels."""

    MEETS_STRONG_INTEGRITY = "MEETS_STRONG_INTEGRITY"
    MEETS_DEVICE_INTEGRITY = "MEETS_DEVICE_INTEGRITY"
    MEETS_BASIC_INTEGRITY = "MEETS_BASIC_INTEGRITY"


class AppRecognitionVerdict(str, Enum):
    """App integrity verdict."""

    PLAY_RECOGNIZED = "PLAY_RECOGNIZED"
    UNRECOGNIZED_VERSION = "UNRECOGNIZED_VERSION"
    UNEVALUATED = "UNEVALUATED"


class AppLicensingVerdict(str, Enum):
    """App licensing verdict."""

    LICENSED = "LICENSED"
    UNLICENSED = "UNLICENSED"
    UNEVALUATED = "UNEVALUATED"


# ============================================================================
# API Request/Response Models
# ============================================================================


class IntegrityNonceRequest(BaseModel):
    """Request for a new integrity nonce."""

    # Optional context about why nonce is needed
    context: str | None = Field(
        None,
        description="Context for the nonce (e.g., 'purchase', 'login', 'credit_check')",
    )


class IntegrityNonceResponse(BaseModel):
    """Response with a new integrity nonce."""

    nonce: str = Field(..., description="Base64 URL-safe encoded nonce for integrity request")
    expires_at: datetime = Field(..., description="When this nonce expires")


class IntegrityVerifyRequest(BaseModel):
    """Request to verify a Play Integrity token."""

    integrity_token: str = Field(..., description="The encrypted integrity token from Android")
    nonce: str = Field(..., description="The nonce that was used to request this token")


class DeviceIntegrityResult(BaseModel):
    """Device integrity verdict details."""

    meets_strong_integrity: bool = False
    meets_device_integrity: bool = False
    meets_basic_integrity: bool = False
    verdicts: list[str] = Field(default_factory=list)


class AppIntegrityResult(BaseModel):
    """App integrity verdict details."""

    verdict: str
    package_name: str | None = None
    certificate_sha256_digest: list[str] = Field(default_factory=list)
    version_code: int | None = None


class AccountIntegrityResult(BaseModel):
    """Account/licensing verdict details."""

    licensing_verdict: str


class IntegrityVerifyResponse(BaseModel):
    """Response from Play Integrity verification."""

    verified: bool = Field(..., description="Whether the integrity check passed")
    request_details: dict[str, str] | None = Field(None, description="Request metadata from token")
    device_integrity: DeviceIntegrityResult | None = None
    app_integrity: AppIntegrityResult | None = None
    account_details: AccountIntegrityResult | None = None
    error: str | None = Field(None, description="Error message if verification failed")


# ============================================================================
# Combined Auth + Integrity Models
# ============================================================================


class IntegrityAuthRequest(BaseModel):
    """
    Combined JWT + Integrity verification for high-security operations.

    Used for:
    - First app launch / registration
    - Before processing payments
    - Granting premium features
    """

    integrity_token: str = Field(..., description="Play Integrity token from Android")
    nonce: str = Field(..., description="Nonce used to request the integrity token")


class IntegrityAuthResponse(BaseModel):
    """Response from combined auth + integrity verification."""

    authenticated: bool = Field(..., description="JWT authentication passed")
    integrity_verified: bool = Field(..., description="Play Integrity check passed")
    user_id: str | None = Field(None, description="Google user ID from JWT")
    email: str | None = Field(None, description="User email from JWT")

    # Integrity details
    device_integrity: DeviceIntegrityResult | None = None
    app_integrity: AppIntegrityResult | None = None

    # Overall status
    authorized: bool = Field(..., description="Both auth and integrity passed")
    reason: str | None = Field(None, description="Reason if not authorized")


# ============================================================================
# Database/Internal Models
# ============================================================================


class IntegrityNonce(BaseModel):
    """Internal model for tracking nonces."""

    nonce: str
    created_at: datetime
    expires_at: datetime
    context: str | None = None
    used: bool = False
    used_at: datetime | None = None
    account_id: UUID | None = None  # If associated with a user
