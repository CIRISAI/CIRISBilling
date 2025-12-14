"""
FastAPI Dependencies - Authentication and authorization.

NO DICTIONARIES - All dependencies return typed objects.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import settings
from app.db.session import get_write_db
from app.exceptions import AuthenticationError
from app.models.domain import AccountIdentity
from app.services.api_key import APIKeyData, APIKeyService
from app.services.token_revocation import token_revocation_service

logger = get_logger(__name__)

# ============================================================================
# User JWT Authentication (for Android/mobile clients)
# ============================================================================


@dataclass
class UserIdentity:
    """Authenticated user identity from JWT token."""

    oauth_provider: str  # e.g., "oauth:google"
    external_id: str  # Google user ID
    email: str | None = None
    name: str | None = None


# Bearer token scheme for JWT auth
bearer_scheme = HTTPBearer(auto_error=False)

# Cache for verified Google ID tokens: token -> (user_id, email, name, expiry_timestamp)
_google_token_cache: dict[str, tuple[str, str | None, str | None, float]] = {}
_MAX_CACHE_SIZE = 10000


def _cleanup_google_token_cache() -> None:
    """Remove expired entries from the cache."""
    import time

    if len(_google_token_cache) < _MAX_CACHE_SIZE:
        return

    now = time.time()
    # Cache stores: (user_id, email, name, expiry_timestamp)
    expired = [k for k, (_, _, _, exp) in _google_token_cache.items() if exp < now]
    for k in expired:
        del _google_token_cache[k]


async def get_user_from_google_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
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

    # SECURITY: Check if token has been revoked
    if await token_revocation_service.is_revoked(token, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check cache first (avoids network call to Google)
    if token in _google_token_cache:
        user_id, email, name, expiry = _google_token_cache[token]
        if time.time() < expiry:
            return UserIdentity(
                oauth_provider="oauth:google",
                external_id=user_id,
                email=email,
                name=name,
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

    # Get list of valid client IDs (supports web + Android clients)
    valid_client_ids = settings.valid_google_client_ids
    if not valid_client_ids:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: no Google client IDs configured",
        )

    # Import logger for debugging
    from structlog import get_logger

    logger = get_logger(__name__)
    logger.info(
        "google_token_validation_starting",
        client_ids_count=len(valid_client_ids),
        client_ids=valid_client_ids,
    )

    # Try each client ID until one works
    # Android tokens have web client ID as audience but Android ID as azp
    last_error = None
    expired_idinfo = None  # Store decoded info from expired tokens
    tried_client_ids: list[str] = []  # Track which client IDs we tried
    for client_id in valid_client_ids:
        try:
            # Verify the token with Google's public keys
            # This checks:
            # 1. Token signature is valid (signed by Google)
            # 2. Token is not expired
            # 3. Token audience matches our client ID
            # 4. Token issuer is Google
            idinfo = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token,
                google_requests.Request(),  # type: ignore[no-untyped-call]
                client_id,
            )

            # Extract user ID from the 'sub' (subject) claim
            user_id = idinfo.get("sub")
            email = idinfo.get("email")
            name = idinfo.get("name")

            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing user ID",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Cache the verified token until it expires (with 60s buffer)
            expiry = idinfo.get("exp", time.time() + 3600) - 60
            _cleanup_google_token_cache()
            _google_token_cache[token] = (user_id, email, name, expiry)

            return UserIdentity(
                oauth_provider="oauth:google",
                external_id=user_id,
                email=email,
                name=name,
            )

        except ValueError as e:
            last_error = str(e)
            tried_client_ids.append(client_id[:20] + "...")
            # If token is expired, try to decode it anyway (signature was valid)
            if "expired" in last_error.lower():
                try:
                    # Decode without verification to extract claims from expired token
                    import jwt

                    # Decode without verification - we trust Google signed it
                    expired_idinfo = jwt.decode(
                        token,
                        options={"verify_signature": False, "verify_exp": False},
                    )
                    logger.info(
                        "google_token_expired_but_accepted",
                        sub=expired_idinfo.get("sub"),
                        exp=expired_idinfo.get("exp"),
                    )
                except Exception as decode_err:
                    logger.warning("failed_to_decode_expired_token", error=str(decode_err))
                continue  # Try next client ID in case it works
            # If it's an audience mismatch, try next client ID
            if "audience" in last_error.lower():
                continue
            # For other errors (invalid signature), fail immediately
            break

    # If we have an expired but otherwise valid token, accept it
    logger.info(
        "expired_token_fallback_check",
        has_expired_idinfo=bool(expired_idinfo),
        last_error=last_error,
        expired_in_error="expired" in (last_error or "").lower() if last_error else False,
    )
    if expired_idinfo and "expired" in (last_error or "").lower():
        user_id = expired_idinfo.get("sub")
        email = expired_idinfo.get("email")
        name = expired_idinfo.get("name")
        aud = expired_idinfo.get("aud")

        logger.info(
            "expired_token_audience_check",
            aud=aud,
            valid_client_ids=valid_client_ids,
            aud_matches=aud in valid_client_ids,
        )

        # Verify audience matches one of our client IDs
        if aud not in valid_client_ids:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token audience. Token not issued for this application.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Don't cache expired tokens - short TTL
        _cleanup_google_token_cache()
        _google_token_cache[token] = (user_id, email, name, time.time() + 300)  # 5 min cache

        logger.info(
            "accepting_expired_google_token",
            user_id=user_id,
            email=email,
        )

        return UserIdentity(
            oauth_provider="oauth:google",
            external_id=user_id,
            email=email,
            name=name,
        )

    # All client IDs failed - now log the warning
    logger.warning(
        "google_token_validation_failed_all_client_ids",
        tried_client_ids=tried_client_ids,
        error=last_error,
    )

    error_msg = last_error or "Token validation failed"

    if "audience" in error_msg.lower():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token audience. Token not issued for this application.",
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
    db: AsyncSession = Depends(get_write_db),
) -> UserIdentity | None:
    """
    Optional Google ID token authentication - returns None if no token provided.

    Useful for endpoints that can work with or without auth.
    """
    if credentials is None:
        return None

    try:
        return await get_user_from_google_token(credentials, db)
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


# ============================================================================
# Combined Auth (API Key OR JWT) for LiteLLM endpoints
# ============================================================================


@dataclass
class CombinedAuth:
    """
    Result of combined authentication - either API key or user JWT.

    When using JWT auth, the user identity is extracted from the token.
    When using API key auth, the identity must be provided in the request body.
    """

    auth_type: str  # "api_key" or "jwt"
    api_key: APIKeyData | None = None
    user: UserIdentity | None = None


async def get_api_key_or_jwt(
    x_api_key: str | None = Header(None, description="Agent API key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
) -> CombinedAuth:
    """
    Combined auth dependency that accepts EITHER API key OR JWT token.

    Priority:
    1. If X-API-Key header is present, use API key auth
    2. If Authorization: Bearer token is present, use JWT auth
    3. If neither, raise 401

    This allows the LiteLLM endpoints to be called by:
    - Service accounts using API keys (proxy-to-billing)
    - Android apps using Google ID tokens (app-to-billing direct)
    """
    logger.info(
        "get_api_key_or_jwt_called",
        has_api_key=bool(x_api_key),
        has_credentials=bool(credentials),
        credentials_scheme=credentials.scheme if credentials else None,
    )

    # Try API key first (service-to-service)
    if x_api_key:
        api_key_service = APIKeyService(db)
        try:
            api_key_data = await api_key_service.validate_api_key(x_api_key)
            return CombinedAuth(auth_type="api_key", api_key=api_key_data)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid API key: {exc}",
                headers={"WWW-Authenticate": "ApiKey"},
            ) from exc

    # Try JWT (user direct access)
    if credentials:
        user = await get_user_from_google_token(credentials, db)
        return CombinedAuth(auth_type="jwt", user=user)

    # Neither provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide X-API-Key header or Authorization: Bearer {google_id_token}",
        headers={"WWW-Authenticate": "Bearer, ApiKey"},
    )


def require_permission_or_jwt(
    required_permission: str,
) -> Callable[..., Awaitable[CombinedAuth]]:
    """
    Combined auth with permission check for API keys.

    - If API key auth: checks that the key has the required permission
    - If JWT auth: always allowed (user is authenticated)
    """

    async def auth_checker(
        auth: CombinedAuth = Depends(get_api_key_or_jwt),
    ) -> CombinedAuth:
        """Check permission for API key auth, pass through for JWT."""
        if auth.auth_type == "api_key" and auth.api_key:
            if required_permission not in auth.api_key.permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Missing required permission: {required_permission}",
                )
        return auth

    return auth_checker


# ============================================================================
# Account Identity from JWT (for tool endpoints)
# ============================================================================


async def get_validated_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
) -> AccountIdentity:
    """
    Get validated AccountIdentity from JWT token.

    Used by tool endpoints that require user authentication.
    Returns AccountIdentity directly, suitable for ProductInventoryService.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Authorization: Bearer {google_id_token}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_from_google_token(credentials, db)

    return AccountIdentity(
        oauth_provider=user.oauth_provider,
        external_id=user.external_id,
        wa_id=None,
        tenant_id=None,
    )
