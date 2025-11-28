"""
FastAPI Dependencies - Authentication and authorization.

NO DICTIONARIES - All dependencies return typed objects.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_write_db
from app.exceptions import AuthenticationError
from app.services.api_key import APIKeyData, APIKeyService

# ============================================================================
# User JWT Authentication (for Android/mobile clients)
# ============================================================================


@dataclass
class UserIdentity:
    """Authenticated user identity from JWT token."""

    oauth_provider: str  # e.g., "oauth:google"
    external_id: str  # Google user ID
    email: str | None = None


# Bearer token scheme for JWT auth
bearer_scheme = HTTPBearer(auto_error=False)

# Cache for verified Google ID tokens: token -> (user_id, email, expiry_timestamp)
_google_token_cache: dict[str, tuple[str, str | None, float]] = {}
_MAX_CACHE_SIZE = 10000


def _cleanup_google_token_cache() -> None:
    """Remove expired entries from the cache."""
    import time

    if len(_google_token_cache) < _MAX_CACHE_SIZE:
        return

    now = time.time()
    expired = [k for k, (_, _, exp) in _google_token_cache.items() if exp < now]
    for k in expired:
        del _google_token_cache[k]


async def get_user_from_google_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserIdentity:
    """
    FastAPI dependency to validate Google ID token from Authorization header.

    Accepts: Authorization: Bearer {google_id_token}
    Verifies: Token signature against Google's public keys
    Extracts: User's Google ID (sub claim) for billing

    This is the same auth method used by CIRISProxy, allowing Android clients
    to use the same Google ID token for both LLM requests and billing operations.

    Usage:
        @router.get("/v1/user/balance")
        async def get_my_balance(
            user: UserIdentity = Depends(get_user_from_google_token)
        ):
            # user.external_id is the Google user ID
            pass

    Returns:
        UserIdentity if valid Google ID token

    Raises:
        HTTPException 401 if no token or invalid token
    """
    import time

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Check cache first (avoids network call to Google)
    if token in _google_token_cache:
        user_id, email, expiry = _google_token_cache[token]
        if time.time() < expiry:
            return UserIdentity(
                oauth_provider="oauth:google",
                external_id=user_id,
                email=email,
            )
        else:
            # Token expired, remove from cache
            del _google_token_cache[token]

    # Import Google auth library
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: google-auth not installed",
        )

    try:
        # Verify the token with Google's public keys
        # This checks:
        # 1. Token signature is valid (signed by Google)
        # 2. Token is not expired
        # 3. Token audience matches our client ID
        # 4. Token issuer is Google
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )

        # Extract user ID from the 'sub' (subject) claim
        user_id = idinfo.get("sub")
        email = idinfo.get("email")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Cache the verified token until it expires (with 60s buffer)
        expiry = idinfo.get("exp", time.time() + 3600) - 60
        _cleanup_google_token_cache()
        _google_token_cache[token] = (user_id, email, expiry)

        return UserIdentity(
            oauth_provider="oauth:google",
            external_id=user_id,
            email=email,
        )

    except ValueError as e:
        error_msg = str(e)

        if "Token has expired" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired. Please refresh your authentication.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        elif "audience" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token audience. Please use the correct app.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid Google ID token: {error_msg}",
                headers={"WWW-Authenticate": "Bearer"},
            )


async def get_optional_user_from_google_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserIdentity | None:
    """
    Optional Google ID token authentication - returns None if no token provided.

    Useful for endpoints that can work with or without auth.
    """
    if credentials is None:
        return None

    try:
        return await get_user_from_google_token(credentials)
    except HTTPException:
        return None


# ============================================================================
# API Key Authentication (for service-to-service)
# ============================================================================


async def get_api_key(
    x_api_key: str = Header(..., description="Agent API key"),
    db: AsyncSession = Depends(get_write_db),
) -> APIKeyData:
    """
    FastAPI dependency to validate API key from X-API-Key header.

    Usage:
        @router.post("/v1/billing/charges")
        async def create_charge(
            request: CreateChargeRequest,
            api_key: APIKeyData = Depends(get_api_key)
        ):
            # api_key is validated and contains permissions
            pass

    Returns:
        APIKeyData if valid

    Raises:
        HTTPException 401 if invalid
    """
    api_key_service = APIKeyService(db)

    try:
        api_key_data = await api_key_service.validate_api_key(x_api_key)
        return api_key_data
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "ApiKey"},
        ) from exc


def require_permission(required_permission: str) -> Callable[..., Awaitable[APIKeyData]]:
    """
    FastAPI dependency factory to check specific permission.

    Usage:
        @router.post("/v1/billing/charges")
        async def create_charge(
            request: CreateChargeRequest,
            api_key: APIKeyData = Depends(require_permission("billing:write"))
        ):
            pass

    Args:
        required_permission: Permission string (e.g., "billing:write")

    Returns:
        Dependency function that checks permission
    """

    async def permission_checker(
        api_key: APIKeyData = Depends(get_api_key),
    ) -> APIKeyData:
        """Check if API key has required permission."""
        if required_permission not in api_key.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {required_permission}",
            )
        return api_key

    return permission_checker
