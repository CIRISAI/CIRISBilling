"""
Tests for API Dependencies.

Tests authentication and authorization dependencies.
"""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.dependencies import (
    CombinedAuth,
    UserIdentity,
    _cleanup_google_token_cache,
    _get_cached_user,
    _google_token_cache,
    _raise_auth_error,
    get_api_key,
    get_api_key_or_jwt,
    get_optional_user_from_google_token,
    get_user_from_google_token,
    get_validated_identity,
    require_permission,
    require_permission_or_jwt,
)
from app.exceptions import AuthenticationError
from app.services.api_key import APIKeyData


class TestUserIdentity:
    """Tests for UserIdentity dataclass."""

    def test_user_identity_all_fields(self):
        """UserIdentity with all fields set."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
            email="test@example.com",
            name="Test User",
        )
        assert user.oauth_provider == "oauth:google"
        assert user.external_id == "user123"
        assert user.email == "test@example.com"
        assert user.name == "Test User"

    def test_user_identity_minimal(self):
        """UserIdentity with only required fields."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
        )
        assert user.oauth_provider == "oauth:google"
        assert user.external_id == "user123"
        assert user.email is None
        assert user.name is None


class TestRaiseAuthError:
    """Tests for _raise_auth_error helper."""

    def test_raises_401(self):
        """_raise_auth_error raises HTTPException with 401."""
        with pytest.raises(HTTPException) as exc_info:
            _raise_auth_error("Test error message")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Test error message"
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


class TestGoogleTokenCache:
    """Tests for Google token cache functions."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear token cache before and after each test."""
        _google_token_cache.clear()
        yield
        _google_token_cache.clear()

    def test_get_cached_user_not_found(self):
        """_get_cached_user returns None for non-existent token."""
        result = _get_cached_user("nonexistent-token")
        assert result is None

    def test_get_cached_user_valid(self):
        """_get_cached_user returns UserIdentity for valid cached token."""
        # Add token to cache with future expiry
        token = "test-token"
        expiry = time.time() + 3600
        _google_token_cache[token] = ("user123", "test@example.com", "Test", expiry)

        result = _get_cached_user(token)

        assert result is not None
        assert result.oauth_provider == "oauth:google"
        assert result.external_id == "user123"
        assert result.email == "test@example.com"
        assert result.name == "Test"

    def test_get_cached_user_expired(self):
        """_get_cached_user removes and returns None for expired token."""
        token = "expired-token"
        expiry = time.time() - 100  # Expired
        _google_token_cache[token] = ("user123", "test@example.com", "Test", expiry)

        result = _get_cached_user(token)

        assert result is None
        assert token not in _google_token_cache

    def test_cleanup_skipped_if_below_max(self):
        """_cleanup_google_token_cache does nothing if cache is small."""
        _google_token_cache["token1"] = ("user1", None, None, time.time() - 100)

        _cleanup_google_token_cache()

        # Expired token still there because cache < MAX_SIZE
        assert "token1" in _google_token_cache

    def test_cleanup_removes_expired(self):
        """_cleanup_google_token_cache removes expired entries when at max size."""
        # Fill cache to max size
        now = time.time()
        for i in range(10001):
            _google_token_cache[f"token{i}"] = ("user", None, None, now - 100)

        # Add one valid token
        _google_token_cache["valid"] = ("user", None, None, now + 3600)

        _cleanup_google_token_cache()

        # Only valid token should remain
        assert "valid" in _google_token_cache
        assert "token0" not in _google_token_cache


class TestGetUserFromGoogleToken:
    """Tests for get_user_from_google_token."""

    @pytest.fixture
    def mock_credentials(self):
        """Create mock HTTP credentials."""
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "test-jwt-token"
        return creds

    @pytest.fixture
    def db_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear token cache before each test."""
        _google_token_cache.clear()
        yield
        _google_token_cache.clear()

    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self, db_session: AsyncMock):
        """Missing credentials raises 401."""
        with pytest.raises(HTTPException) as exc_info:
            await get_user_from_google_token(None, db_session)

        assert exc_info.value.status_code == 401
        assert "Authorization header required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_revoked_token_raises_401(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Revoked token raises 401."""
        with patch("app.api.dependencies.token_revocation_service") as mock_service:
            mock_service.is_revoked = AsyncMock(return_value=True)

            with pytest.raises(HTTPException) as exc_info:
                await get_user_from_google_token(mock_credentials, db_session)

            assert exc_info.value.status_code == 401
            assert "revoked" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_cached_token_returns_user(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Cached token returns user without calling Google."""
        # Pre-cache the token
        token = mock_credentials.credentials
        expiry = time.time() + 3600
        _google_token_cache[token] = ("user123", "test@example.com", "Test User", expiry)

        with patch("app.api.dependencies.token_revocation_service") as mock_service:
            mock_service.is_revoked = AsyncMock(return_value=False)

            result = await get_user_from_google_token(mock_credentials, db_session)

            assert result.oauth_provider == "oauth:google"
            assert result.external_id == "user123"
            assert result.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_no_client_ids_configured_raises_500(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Missing client IDs configuration raises 500."""
        with patch("app.api.dependencies.token_revocation_service") as mock_service:
            mock_service.is_revoked = AsyncMock(return_value=False)

            with patch("app.api.dependencies.settings") as mock_settings:
                mock_settings.valid_google_client_ids = []
                mock_settings.CIRIS_TEST_AUTH_ENABLED = False
                mock_settings.CIRIS_TEST_AUTH_TOKEN = ""

                with pytest.raises(HTTPException) as exc_info:
                    await get_user_from_google_token(mock_credentials, db_session)

                assert exc_info.value.status_code == 500
                assert "no Google client IDs" in exc_info.value.detail


class TestGetOptionalUserFromGoogleToken:
    """Tests for get_optional_user_from_google_token."""

    @pytest.mark.asyncio
    async def test_no_credentials_returns_none(self):
        """No credentials returns None."""
        db_session = AsyncMock()

        result = await get_optional_user_from_google_token(None, db_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self):
        """Invalid token returns None instead of raising."""
        db_session = AsyncMock()
        credentials = MagicMock(spec=HTTPAuthorizationCredentials)
        credentials.credentials = "invalid-token"

        with patch(
            "app.api.dependencies.get_user_from_google_token",
            side_effect=HTTPException(status_code=401, detail="Invalid"),
        ):
            result = await get_optional_user_from_google_token(credentials, db_session)

        assert result is None


class TestGetApiKey:
    """Tests for get_api_key."""

    @pytest.mark.asyncio
    async def test_valid_key_returns_data(self):
        """Valid API key returns APIKeyData."""
        db_session = AsyncMock()
        expected_data = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read", "billing:write"],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )

        with patch("app.api.dependencies.APIKeyService") as MockService:
            service = MockService.return_value
            service.validate_api_key = AsyncMock(return_value=expected_data)

            result = await get_api_key("cbk_test_validkey123", db_session)

            assert result == expected_data
            service.validate_api_key.assert_called_once_with("cbk_test_validkey123")

    @pytest.mark.asyncio
    async def test_invalid_key_raises_401(self):
        """Invalid API key raises 401."""
        db_session = AsyncMock()

        with patch("app.api.dependencies.APIKeyService") as MockService:
            service = MockService.return_value
            service.validate_api_key = AsyncMock(
                side_effect=AuthenticationError("Invalid API key format")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_api_key("invalid-key", db_session)

            assert exc_info.value.status_code == 401
            assert "Invalid API key format" in exc_info.value.detail


class TestRequirePermission:
    """Tests for require_permission dependency factory."""

    @pytest.mark.asyncio
    async def test_permission_granted(self):
        """API key with required permission is allowed."""
        api_key = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read", "billing:write"],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )

        checker = require_permission("billing:write")
        result = await checker(api_key)

        assert result == api_key

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        """API key without required permission raises 403."""
        api_key = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read"],  # Missing billing:write
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )

        checker = require_permission("billing:write")

        with pytest.raises(HTTPException) as exc_info:
            await checker(api_key)

        assert exc_info.value.status_code == 403
        assert "billing:write" in exc_info.value.detail


class TestCombinedAuth:
    """Tests for CombinedAuth dataclass."""

    def test_api_key_auth(self):
        """CombinedAuth with API key."""
        api_key = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read"],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )
        auth = CombinedAuth(auth_type="api_key", api_key=api_key, user=None)

        assert auth.auth_type == "api_key"
        assert auth.api_key == api_key
        assert auth.user is None

    def test_jwt_auth(self):
        """CombinedAuth with JWT user."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
            email="test@example.com",
            name="Test User",
        )
        auth = CombinedAuth(auth_type="jwt", api_key=None, user=user)

        assert auth.auth_type == "jwt"
        assert auth.api_key is None
        assert auth.user == user


class TestGetApiKeyOrJwt:
    """Tests for get_api_key_or_jwt."""

    @pytest.mark.asyncio
    async def test_api_key_priority(self):
        """API key takes priority over JWT when both provided."""
        db_session = AsyncMock()
        credentials = MagicMock(spec=HTTPAuthorizationCredentials)
        credentials.credentials = "jwt-token"
        credentials.scheme = "Bearer"

        api_key_data = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read"],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )

        with patch("app.api.dependencies.APIKeyService") as MockService:
            service = MockService.return_value
            service.validate_api_key = AsyncMock(return_value=api_key_data)

            result = await get_api_key_or_jwt(
                x_api_key="cbk_test_validkey",
                credentials=credentials,
                db=db_session,
            )

            assert result.auth_type == "api_key"
            assert result.api_key == api_key_data

    @pytest.mark.asyncio
    async def test_jwt_when_no_api_key(self):
        """JWT used when no API key provided."""
        db_session = AsyncMock()
        credentials = MagicMock(spec=HTTPAuthorizationCredentials)
        credentials.credentials = "jwt-token"
        credentials.scheme = "Bearer"

        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
            email="test@example.com",
            name="Test User",
        )

        with patch("app.api.dependencies.get_user_from_google_token") as mock_get_user:
            mock_get_user.return_value = user

            result = await get_api_key_or_jwt(
                x_api_key=None,
                credentials=credentials,
                db=db_session,
            )

            assert result.auth_type == "jwt"
            assert result.user == user

    @pytest.mark.asyncio
    async def test_no_auth_raises_401(self):
        """No auth provided raises 401."""
        db_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_api_key_or_jwt(
                x_api_key=None,
                credentials=None,
                db=db_session,
            )

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_401(self):
        """Invalid API key raises 401."""
        db_session = AsyncMock()

        with patch("app.api.dependencies.APIKeyService") as MockService:
            service = MockService.return_value
            service.validate_api_key = AsyncMock(side_effect=AuthenticationError("Invalid key"))

            with pytest.raises(HTTPException) as exc_info:
                await get_api_key_or_jwt(
                    x_api_key="invalid-key",
                    credentials=None,
                    db=db_session,
                )

            assert exc_info.value.status_code == 401


class TestRequirePermissionOrJwt:
    """Tests for require_permission_or_jwt."""

    @pytest.mark.asyncio
    async def test_api_key_with_permission(self):
        """API key with required permission passes."""
        api_key = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:write"],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )
        auth = CombinedAuth(auth_type="api_key", api_key=api_key, user=None)

        checker = require_permission_or_jwt("billing:write")
        result = await checker(auth)

        assert result == auth

    @pytest.mark.asyncio
    async def test_api_key_without_permission(self):
        """API key without required permission raises 403."""
        api_key = APIKeyData(
            key_id=uuid4(),
            name="Test Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=["billing:read"],  # Missing billing:write
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )
        auth = CombinedAuth(auth_type="api_key", api_key=api_key, user=None)

        checker = require_permission_or_jwt("billing:write")

        with pytest.raises(HTTPException) as exc_info:
            await checker(auth)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_jwt_always_allowed(self):
        """JWT auth always allowed regardless of permission."""
        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
        )
        auth = CombinedAuth(auth_type="jwt", api_key=None, user=user)

        checker = require_permission_or_jwt("billing:write")
        result = await checker(auth)

        assert result == auth


class TestGetValidatedIdentity:
    """Tests for get_validated_identity."""

    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self):
        """No credentials raises 401."""
        db_session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_validated_identity(None, db_session)

        assert exc_info.value.status_code == 401
        assert "Authentication required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_credentials_returns_identity(self):
        """Valid credentials returns AccountIdentity."""
        db_session = AsyncMock()
        credentials = MagicMock(spec=HTTPAuthorizationCredentials)
        credentials.credentials = "jwt-token"

        user = UserIdentity(
            oauth_provider="oauth:google",
            external_id="user123",
            email="test@example.com",
        )

        with patch("app.api.dependencies.get_user_from_google_token") as mock_get_user:
            mock_get_user.return_value = user

            result = await get_validated_identity(credentials, db_session)

            assert result.oauth_provider == "oauth:google"
            assert result.external_id == "user123"
            assert result.wa_id is None
            assert result.tenant_id is None


class TestTestTokenAuthentication:
    """Tests for test token authentication (development/testing feature)."""

    @pytest.fixture
    def db_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.fixture
    def mock_credentials(self):
        """Create mock HTTP credentials with test token."""
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = "a" * 64  # Valid test token (64+ chars)
        return creds

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear token cache before each test."""
        _google_token_cache.clear()
        yield
        _google_token_cache.clear()

    @pytest.mark.asyncio
    async def test_test_token_disabled_by_default(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Test token auth is disabled when CIRIS_TEST_AUTH_ENABLED is False."""
        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = False
            mock_settings.CIRIS_TEST_AUTH_TOKEN = "a" * 64
            mock_settings.valid_google_client_ids = []

            with patch("app.api.dependencies.token_revocation_service") as mock_revoke:
                mock_revoke.is_revoked = AsyncMock(return_value=False)

                # Should fall through to Google auth (and fail without client IDs)
                with pytest.raises(HTTPException) as exc_info:
                    await get_user_from_google_token(mock_credentials, db_session)

                assert exc_info.value.status_code == 500
                assert "no Google client IDs" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_test_token_enabled_and_matching(
        self,
        db_session: AsyncMock,
    ):
        """Test token auth succeeds when enabled and token matches."""
        test_token = "b" * 64
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = test_token

        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = True
            mock_settings.CIRIS_TEST_AUTH_TOKEN = test_token
            mock_settings.CIRIS_TEST_USER_ID = "test-user-123"

            result = await get_user_from_google_token(creds, db_session)

            assert result.oauth_provider == "oauth:test"
            assert result.external_id == "test-user-123"
            assert result.email == "test-user-123@test.ciris.ai"
            assert result.name == "Automated Test User"

    @pytest.mark.asyncio
    async def test_test_token_enabled_but_not_matching(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Test token auth falls through to Google auth when token doesn't match."""
        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = True
            mock_settings.CIRIS_TEST_AUTH_TOKEN = "different_token_" + "x" * 49
            mock_settings.valid_google_client_ids = []

            with patch("app.api.dependencies.token_revocation_service") as mock_revoke:
                mock_revoke.is_revoked = AsyncMock(return_value=False)

                # Should fall through to Google auth (and fail without client IDs)
                with pytest.raises(HTTPException) as exc_info:
                    await get_user_from_google_token(mock_credentials, db_session)

                # Fails on Google auth, not test token
                assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_test_token_default_user_id(
        self,
        db_session: AsyncMock,
    ):
        """Test token uses default user ID when CIRIS_TEST_USER_ID not set."""
        test_token = "c" * 64
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = test_token

        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = True
            mock_settings.CIRIS_TEST_AUTH_TOKEN = test_token
            mock_settings.CIRIS_TEST_USER_ID = ""  # Not set

            result = await get_user_from_google_token(creds, db_session)

            assert result.external_id == "test-user-automated"

    @pytest.mark.asyncio
    async def test_test_token_no_token_configured(
        self,
        mock_credentials: MagicMock,
        db_session: AsyncMock,
    ):
        """Test token auth falls through when no token configured."""
        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = True
            mock_settings.CIRIS_TEST_AUTH_TOKEN = ""  # Empty
            mock_settings.valid_google_client_ids = []

            with patch("app.api.dependencies.token_revocation_service") as mock_revoke:
                mock_revoke.is_revoked = AsyncMock(return_value=False)

                # Should fall through to Google auth
                with pytest.raises(HTTPException) as exc_info:
                    await get_user_from_google_token(mock_credentials, db_session)

                assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_test_token_logs_warning(
        self,
        db_session: AsyncMock,
    ):
        """Test token auth logs a warning for audit purposes."""
        test_token = "d" * 64
        creds = MagicMock(spec=HTTPAuthorizationCredentials)
        creds.credentials = test_token

        with patch("app.api.dependencies.settings") as mock_settings:
            mock_settings.CIRIS_TEST_AUTH_ENABLED = True
            mock_settings.CIRIS_TEST_AUTH_TOKEN = test_token
            mock_settings.CIRIS_TEST_USER_ID = "audit-test-user"

            with patch("app.api.dependencies.logger") as mock_logger:
                result = await get_user_from_google_token(creds, db_session)

                # Verify warning was logged
                mock_logger.warning.assert_called_once()
                call_args = mock_logger.warning.call_args
                assert call_args[0][0] == "test_token_auth_used"
                assert call_args[1]["external_id"] == "audit-test-user"
                assert call_args[1]["oauth_provider"] == "oauth:test"
