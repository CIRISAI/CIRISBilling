"""
FastAPI Dependencies - Authentication and authorization.

NO DICTIONARIES - All dependencies return typed objects.
"""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import NoReturn

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

# Explicit re-exports for mypy strict mode
__all__ = [
    "APIKeyData",
    "get_validated_identity",
    "get_user_from_apple_token",
    "get_user_from_google_token",
    "get_user_from_oauth_token",
    "require_permission",
    "UserIdentity",
]

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
# Cache for verified Apple ID tokens: token -> (user_id, email, name, expiry_timestamp)
_apple_token_cache: dict[str, tuple[str, str | None, str | None, float]] = {}
# Cache for Apple's public keys: kid -> RSA public key
_apple_public_keys: dict[str, object] = {}
_apple_keys_fetched_at: float = 0
_APPLE_KEYS_CACHE_TTL = 3600  # Refresh keys every hour
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


def _cleanup_apple_token_cache() -> None:
    """Remove expired entries from the Apple token cache."""
    import time

    if len(_apple_token_cache) < _MAX_CACHE_SIZE:
        return

    now = time.time()
    expired = [k for k, (_, _, _, exp) in _apple_token_cache.items() if exp < now]
    for k in expired:
        del _apple_token_cache[k]


async def _fetch_apple_public_keys() -> None:
    """Fetch and cache Apple's public keys for JWT verification."""
    import time

    import httpx

    global _apple_keys_fetched_at

    now = time.time()
    if _apple_public_keys and (now - _apple_keys_fetched_at) < _APPLE_KEYS_CACHE_TTL:
        return  # Keys are still fresh

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://appleid.apple.com/auth/keys",
                timeout=10.0,
            )
            response.raise_for_status()
            keys_data = response.json()

        # Import jwt algorithms for JWK parsing
        from jwt import algorithms

        # Parse JWK keys into RSA public keys
        _apple_public_keys.clear()
        for key_dict in keys_data.get("keys", []):
            kid = key_dict.get("kid")
            if kid:
                # Convert JWK to RSA public key
                public_key = algorithms.RSAAlgorithm.from_jwk(key_dict)
                _apple_public_keys[kid] = public_key

        _apple_keys_fetched_at = now
        logger.info("apple_public_keys_fetched", key_count=len(_apple_public_keys))

    except Exception as e:
        logger.error("apple_public_keys_fetch_failed", error=str(e))
        # Don't clear existing keys on error - use stale keys if available
        if not _apple_public_keys:
            raise


def _get_cached_apple_user(token: str) -> UserIdentity | None:
    """Check cache for a valid Apple token. Returns UserIdentity if cached and not expired."""
    import time

    if token not in _apple_token_cache:
        return None

    user_id, email, name, expiry = _apple_token_cache[token]
    if time.time() < expiry:
        return UserIdentity(
            oauth_provider="oauth:apple",
            external_id=user_id,
            email=email,
            name=name,
        )

    # Token expired, remove from cache
    del _apple_token_cache[token]
    return None


def _get_cached_user(token: str) -> UserIdentity | None:
    """Check cache for a valid token. Returns UserIdentity if cached and not expired."""
    import time

    if token not in _google_token_cache:
        return None

    user_id, email, name, expiry = _google_token_cache[token]
    if time.time() < expiry:
        return UserIdentity(
            oauth_provider="oauth:google",
            external_id=user_id,
            email=email,
            name=name,
        )

    # Token expired, remove from cache
    del _google_token_cache[token]
    return None


def _raise_auth_error(detail: str) -> NoReturn:
    """Raise a 401 HTTPException with WWW-Authenticate header. Always raises."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        _raise_auth_error("Authorization header required")

    token = credentials.credentials

    # Test token authentication (for automated testing only)
    if settings.CIRIS_TEST_AUTH_ENABLED and settings.CIRIS_TEST_AUTH_TOKEN:
        if secrets.compare_digest(token, settings.CIRIS_TEST_AUTH_TOKEN):
            test_user_id = settings.CIRIS_TEST_USER_ID or "test-user-automated"
            logger.warning(
                "test_token_auth_used",
                external_id=test_user_id,
                oauth_provider="oauth:test",
            )
            return UserIdentity(
                oauth_provider="oauth:test",
                external_id=test_user_id,
                email=f"{test_user_id}@test.ciris.ai",
                name="Automated Test User",
            )

    # SECURITY: Check if token has been revoked
    if await token_revocation_service.is_revoked(token, db):
        _raise_auth_error("Token has been revoked")

    # Check cache first (avoids network call to Google)
    cached_user = _get_cached_user(token)
    if cached_user:
        return cached_user

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

    logger.debug(
        "google_token_validation_starting",
        client_ids_count=len(valid_client_ids),
    )

    # Try each client ID until one works
    # Android tokens have web client ID as audience but Android ID as azp
    last_error = None
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
            # If it's an audience mismatch, try next client ID
            if "audience" in last_error.lower():
                continue
            # For other errors (expired, invalid signature), fail immediately
            break

    # All client IDs failed - log and raise appropriate error
    logger.warning(
        "google_token_validation_failed_all_client_ids",
        tried_client_ids=tried_client_ids,
        error=last_error,
    )

    error_msg = last_error or "Token validation failed"
    error_lower = error_msg.lower()

    # Check for expired token - return clear, actionable error
    if "expired" in error_lower or "too late" in error_lower or "too early" in error_lower:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "token_expired",
                "message": "Your session has expired. Please sign in again.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    if "audience" in error_lower:
        _raise_auth_error("Invalid token audience. Token not issued for this application.")
    _raise_auth_error(f"Invalid Google ID token: {error_msg}")


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
# Apple Sign-In Authentication (for iOS clients)
# ============================================================================


async def get_user_from_apple_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
) -> UserIdentity:
    """
    FastAPI dependency to validate Apple ID token from Authorization header.

    Accepts: Authorization: Bearer {apple_id_token}
    Verifies: Token signature against Apple's public keys
    Extracts: User's Apple ID (sub claim) for billing

    Apple Sign-In uses JWT tokens signed with RS256. The token includes:
    - iss: https://appleid.apple.com
    - aud: Your app's bundle ID
    - sub: Unique user identifier (stable across devices)
    - email: User's email (may be hidden/relay address)

    Returns:
        UserIdentity if valid Apple ID token

    Raises:
        HTTPException 401 if no token or invalid token
    """
    import time

    import jwt

    if credentials is None:
        _raise_auth_error("Authorization header required")

    token = credentials.credentials

    # Test token authentication (for automated testing only)
    if settings.CIRIS_TEST_AUTH_ENABLED and settings.CIRIS_TEST_AUTH_TOKEN:
        if secrets.compare_digest(token, settings.CIRIS_TEST_AUTH_TOKEN):
            test_user_id = settings.CIRIS_TEST_USER_ID or "test-user-automated"
            logger.warning(
                "test_token_auth_used",
                external_id=test_user_id,
                oauth_provider="oauth:test",
            )
            return UserIdentity(
                oauth_provider="oauth:test",
                external_id=test_user_id,
                email=f"{test_user_id}@test.ciris.ai",
                name="Automated Test User",
            )

    # SECURITY: Check if token has been revoked
    if await token_revocation_service.is_revoked(token, db):
        _raise_auth_error("Token has been revoked")

    # Check cache first (avoids network call to Apple)
    cached_user = _get_cached_apple_user(token)
    if cached_user:
        return cached_user

    # Get valid bundle IDs
    valid_bundle_ids = settings.valid_apple_client_ids
    if not valid_bundle_ids:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: no Apple client IDs configured",
        )

    # Fetch Apple's public keys (cached)
    try:
        await _fetch_apple_public_keys()
    except Exception as e:
        logger.error("apple_keys_fetch_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch Apple public keys",
        )

    if not _apple_public_keys:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No Apple public keys available",
        )

    # Decode JWT header to get key ID (kid)
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
    except jwt.exceptions.DecodeError as e:
        _raise_auth_error(f"Invalid token format: {e}")

    if not kid:
        _raise_auth_error("Token missing key ID (kid)")

    # Find matching public key
    public_key = _apple_public_keys.get(kid)
    if not public_key:
        # Keys might have rotated, try refreshing
        global _apple_keys_fetched_at
        _apple_keys_fetched_at = 0  # Force refresh
        try:
            await _fetch_apple_public_keys()
            public_key = _apple_public_keys.get(kid)
        except Exception:
            pass

        if not public_key:
            _raise_auth_error(f"Unknown key ID: {kid}")

    logger.debug(
        "apple_token_validation_starting",
        bundle_ids_count=len(valid_bundle_ids),
        kid=kid,
    )

    # Try each bundle ID until one works
    last_error = None
    for bundle_id in valid_bundle_ids:
        try:
            # Verify and decode the token
            payload = jwt.decode(
                token,
                public_key,  # type: ignore[arg-type]  # RSA key stored as object
                algorithms=["RS256"],
                audience=bundle_id,
                issuer="https://appleid.apple.com",
            )

            # Extract user info
            user_id = payload.get("sub")
            email = payload.get("email")
            # Apple doesn't provide name in JWT - only in initial auth response
            # The name would need to be stored/retrieved from the account record
            name = None

            if not user_id:
                _raise_auth_error("Invalid token: missing user ID (sub)")

            # Cache the verified token until it expires (with 60s buffer)
            expiry = payload.get("exp", time.time() + 3600) - 60
            _cleanup_apple_token_cache()
            _apple_token_cache[token] = (user_id, email, name, expiry)

            return UserIdentity(
                oauth_provider="oauth:apple",
                external_id=user_id,
                email=email,
                name=name,
            )

        except jwt.exceptions.InvalidAudienceError:
            last_error = f"Invalid audience for bundle ID {bundle_id[:20]}..."
            continue
        except jwt.exceptions.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "token_expired",
                    "message": "Your session has expired. Please sign in again.",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.exceptions.InvalidTokenError as e:
            last_error = str(e)
            break

    # All bundle IDs failed
    logger.warning(
        "apple_token_validation_failed",
        error=last_error,
    )
    _raise_auth_error(f"Invalid Apple ID token: {last_error}")


async def get_optional_user_from_apple_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
) -> UserIdentity | None:
    """
    Optional Apple ID token authentication - returns None if no token provided.

    Useful for endpoints that can work with or without auth.
    """
    if credentials is None:
        return None

    try:
        return await get_user_from_apple_token(credentials, db)
    except HTTPException:
        return None


# ============================================================================
# Unified OAuth Token Authentication (Google or Apple)
# ============================================================================


async def get_user_from_oauth_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_write_db),
) -> UserIdentity:
    """
    Unified OAuth token authentication - accepts Google or Apple ID tokens.

    The token type is auto-detected based on:
    - JWT issuer claim (Google vs Apple)
    - Token structure

    This allows iOS clients to use Apple Sign-In and Android clients
    to use Google Sign-In with the same endpoint.

    Returns:
        UserIdentity from either Google or Apple token

    Raises:
        HTTPException 401 if no token or invalid token
    """
    import jwt

    if credentials is None:
        _raise_auth_error("Authorization header required")

    token = credentials.credentials

    # Test token authentication (for automated testing only)
    if settings.CIRIS_TEST_AUTH_ENABLED and settings.CIRIS_TEST_AUTH_TOKEN:
        if secrets.compare_digest(token, settings.CIRIS_TEST_AUTH_TOKEN):
            test_user_id = settings.CIRIS_TEST_USER_ID or "test-user-automated"
            logger.warning(
                "test_token_auth_used",
                external_id=test_user_id,
                oauth_provider="oauth:test",
            )
            return UserIdentity(
                oauth_provider="oauth:test",
                external_id=test_user_id,
                email=f"{test_user_id}@test.ciris.ai",
                name="Automated Test User",
            )

    # Check caches first
    cached_google = _get_cached_user(token)
    if cached_google:
        return cached_google

    cached_apple = _get_cached_apple_user(token)
    if cached_apple:
        return cached_apple

    # Try to detect token type by decoding without verification
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss", "")
    except Exception:
        # If we can't decode, try Google first (more common)
        issuer = ""

    # Route to appropriate validator based on issuer
    if issuer == "https://appleid.apple.com":
        return await get_user_from_apple_token(credentials, db)
    elif issuer in ("accounts.google.com", "https://accounts.google.com"):
        return await get_user_from_google_token(credentials, db)
    else:
        # Unknown issuer - try Google first, then Apple
        try:
            return await get_user_from_google_token(credentials, db)
        except HTTPException:
            try:
                return await get_user_from_apple_token(credentials, db)
            except HTTPException:
                _raise_auth_error("Invalid OAuth token. Supported providers: Google, Apple")


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
