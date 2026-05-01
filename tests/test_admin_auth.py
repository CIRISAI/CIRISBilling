"""
Tests for Admin Authentication Service and Routes.

Tests OAuth flow, JWT token management, and admin user handling.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import jwt
import pytest

from app.db.models import AdminOAuthSession, AdminUser
from app.models.domain import OAuthToken, OAuthUser
from app.services.admin_auth import AdminAuthService


def _make_session_row(state: str, redirect_uri: str, callback_url: str, expires_at: datetime) -> AdminOAuthSession:
    """Build an AdminOAuthSession row for tests."""
    return AdminOAuthSession(
        state=state,
        redirect_uri=redirect_uri,
        callback_url=callback_url,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )


def _scalar_result(value):
    """Build a mock result whose scalar_one_or_none returns `value`."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _scalars_all_result(values):
    """Build a mock result whose scalars().all() returns `values`."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = values
    return result


def _delete_result(rowcount: int = 1):
    """Build a mock DELETE result."""
    result = MagicMock()
    result.rowcount = rowcount
    return result


class TestAdminAuthService:
    """Tests for AdminAuthService."""

    @pytest.fixture
    def mock_oauth_provider(self):
        """Create mock OAuth provider."""
        provider = MagicMock()
        provider.get_authorization_url = AsyncMock(
            return_value="https://accounts.google.com/oauth?..."
        )
        return provider

    @pytest.fixture
    def auth_service(self, mock_oauth_provider):
        """Create auth service with mock provider."""
        return AdminAuthService(
            oauth_provider=mock_oauth_provider,
            jwt_secret="test-secret-key-12345",
            jwt_expire_hours=24,
        )

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_persists_state(self, auth_service):
        """initiate_oauth_flow persists state to db and returns auth URL."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        state, auth_url = await auth_service.initiate_oauth_flow(
            redirect_uri="https://example.com/admin",
            callback_url="https://example.com/admin/oauth/callback",
            db=db,
        )

        assert len(state) > 0
        assert auth_url == "https://accounts.google.com/oauth?..."
        # State row was added with correct fields and committed.
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert isinstance(added, AdminOAuthSession)
        assert added.state == state
        assert added.redirect_uri == "https://example.com/admin"
        assert added.expires_at > datetime.now(UTC)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_invalid_state(self, auth_service):
        """handle_oauth_callback raises for invalid state."""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_result(None))

        with pytest.raises(ValueError, match="Invalid OAuth state"):
            await auth_service.handle_oauth_callback(
                code="auth_code",
                state="invalid_state",
                db=db,
            )

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_expired_state(self, auth_service):
        """handle_oauth_callback raises if state is past its TTL."""
        db = AsyncMock()
        expired_row = _make_session_row(
            state="expired_state",
            redirect_uri="https://example.com/admin",
            callback_url="https://example.com/cb",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.execute = AsyncMock(
            side_effect=[_scalar_result(expired_row), _delete_result(1)]
        )
        db.commit = AsyncMock()

        with pytest.raises(ValueError, match="expired"):
            await auth_service.handle_oauth_callback(
                code="auth_code",
                state="expired_state",
                db=db,
            )

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_success(self, auth_service, mock_oauth_provider):
        """handle_oauth_callback completes flow and returns JWT."""
        db = AsyncMock()

        session_row = _make_session_row(
            state="state123",
            redirect_uri="https://example.com/admin",
            callback_url="https://example.com/callback",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        # Mock token exchange
        mock_oauth_provider.exchange_code_for_token = AsyncMock(
            return_value=OAuthToken(
                access_token="google_access_token",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
            )
        )

        # Mock user info
        mock_oauth_provider.get_user_info = AsyncMock(
            return_value=OAuthUser(
                id="google_123",
                email="admin@ciris.ai",
                name="Admin User",
                picture="https://example.com/pic.jpg",
            )
        )

        # Existing admin user
        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = uuid4()
        mock_admin.email = "admin@ciris.ai"
        mock_admin.full_name = "Admin User"
        mock_admin.picture_url = "https://example.com/pic.jpg"
        mock_admin.role = "admin"
        mock_admin.is_active = True
        mock_admin.last_login_at = None
        mock_admin.google_id = None

        # Sequence: SELECT session → DELETE session → SELECT admin user
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(session_row),
                _delete_result(1),
                _scalar_result(mock_admin),
            ]
        )
        db.commit = AsyncMock()

        response = await auth_service.handle_oauth_callback(
            code="auth_code",
            state="state123",
            db=db,
        )

        assert "access_token" in response
        assert response["redirect_uri"] == "https://example.com/admin"
        assert response["user"]["email"] == "admin@ciris.ai"

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_inactive_user(self, auth_service, mock_oauth_provider):
        """handle_oauth_callback raises for inactive user."""
        db = AsyncMock()

        session_row = _make_session_row(
            state="state456",
            redirect_uri="https://example.com/admin",
            callback_url="https://example.com/callback",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        mock_oauth_provider.exchange_code_for_token = AsyncMock(
            return_value=OAuthToken(
                access_token="token",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
            )
        )
        mock_oauth_provider.get_user_info = AsyncMock(
            return_value=OAuthUser(
                id="123",
                email="inactive@ciris.ai",
                name="Inactive",
                picture=None,
            )
        )

        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.is_active = False
        mock_admin.email = "inactive@ciris.ai"
        mock_admin.full_name = "Inactive"
        mock_admin.picture_url = None
        mock_admin.google_id = None

        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(session_row),
                _delete_result(1),
                _scalar_result(mock_admin),
            ]
        )
        db.commit = AsyncMock()

        with pytest.raises(ValueError, match="deactivated"):
            await auth_service.handle_oauth_callback(
                code="auth_code",
                state="state456",
                db=db,
            )

    @pytest.mark.asyncio
    async def test_handle_oauth_callback_creates_new_user(self, auth_service, mock_oauth_provider):
        """handle_oauth_callback creates new user if not exists."""
        db = AsyncMock()

        session_row = _make_session_row(
            state="state789",
            redirect_uri="https://example.com/admin",
            callback_url="https://example.com/callback",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        mock_oauth_provider.exchange_code_for_token = AsyncMock(
            return_value=OAuthToken(
                access_token="token",
                token_type="Bearer",
                expires_in=3600,
                refresh_token=None,
            )
        )
        mock_oauth_provider.get_user_info = AsyncMock(
            return_value=OAuthUser(
                id="123",
                email="newuser@ciris.ai",
                name="New User",
                picture=None,
            )
        )

        # SELECT session → DELETE session → SELECT admin (none) → SELECT all admins (count = 0)
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(session_row),
                _delete_result(1),
                _scalar_result(None),
                _scalars_all_result([]),
            ]
        )
        db.add = MagicMock()
        db.commit = AsyncMock()

        async def mock_refresh(obj):
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = uuid4()

        db.refresh = mock_refresh

        response = await auth_service.handle_oauth_callback(
            code="auth_code",
            state="state789",
            db=db,
        )

        db.add.assert_called_once()
        assert "access_token" in response

    @pytest.mark.asyncio
    async def test_cleanup_expired_oauth_sessions_returns_count(self, auth_service):
        """cleanup_expired_oauth_sessions deletes expired rows and returns count."""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_delete_result(3))
        db.commit = AsyncMock()

        deleted = await auth_service.cleanup_expired_oauth_sessions(db)
        assert deleted == 3
        db.commit.assert_awaited_once()

    def test_create_jwt_token(self, auth_service):
        """_create_jwt_token creates valid JWT."""
        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = uuid4()
        mock_admin.email = "admin@ciris.ai"
        mock_admin.role = "admin"

        token = auth_service._create_jwt_token(mock_admin)

        # Should be valid JWT
        payload = jwt.decode(token, "test-secret-key-12345", algorithms=["HS256"])
        assert payload["email"] == "admin@ciris.ai"
        assert payload["role"] == "admin"
        assert "exp" in payload
        assert "iat" in payload

    def test_verify_jwt_token_valid(self, auth_service):
        """verify_jwt_token returns payload for valid token."""
        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = uuid4()
        mock_admin.email = "admin@ciris.ai"
        mock_admin.role = "admin"

        token = auth_service._create_jwt_token(mock_admin)
        payload = auth_service.verify_jwt_token(token)

        assert payload is not None
        assert payload["email"] == "admin@ciris.ai"

    def test_verify_jwt_token_expired(self, auth_service):
        """verify_jwt_token returns None for expired token."""
        # Create expired token
        now = datetime.now(UTC)
        payload = {
            "sub": str(uuid4()),
            "email": "admin@ciris.ai",
            "role": "admin",
            "iat": now - timedelta(hours=48),
            "exp": now - timedelta(hours=24),  # Expired 24 hours ago
        }
        token = jwt.encode(payload, "test-secret-key-12345", algorithm="HS256")

        result = auth_service.verify_jwt_token(token)
        assert result is None

    def test_verify_jwt_token_invalid(self, auth_service):
        """verify_jwt_token returns None for invalid token."""
        result = auth_service.verify_jwt_token("not.a.valid.token")
        assert result is None

    def test_verify_jwt_token_wrong_secret(self, auth_service):
        """verify_jwt_token returns None for token with wrong secret."""
        # Create token with different secret
        payload = {
            "sub": str(uuid4()),
            "email": "admin@ciris.ai",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
        token = jwt.encode(payload, "wrong-secret", algorithm="HS256")

        result = auth_service.verify_jwt_token(token)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_admin_user_by_id_found(self, auth_service):
        """get_admin_user_by_id returns user when found."""
        db = AsyncMock()
        user_id = uuid4()

        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = user_id
        mock_admin.email = "admin@ciris.ai"

        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_admin
        db.execute = AsyncMock(return_value=result)

        admin_user = await auth_service.get_admin_user_by_id(db, user_id)

        assert admin_user == mock_admin

    @pytest.mark.asyncio
    async def test_get_admin_user_by_id_not_found(self, auth_service):
        """get_admin_user_by_id returns None when not found."""
        db = AsyncMock()

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        admin_user = await auth_service.get_admin_user_by_id(db, uuid4())

        assert admin_user is None


class TestAdminAuthRoutes:
    """Tests for admin auth route handlers."""

    @pytest.fixture
    def mock_auth_service(self):
        """Create mock auth service."""
        service = MagicMock(spec=AdminAuthService)
        return service

    @pytest.mark.asyncio
    async def test_google_login_initiates_flow(self, mock_auth_service):
        """google_login redirects to Google OAuth."""
        from app.api.admin_auth_routes import google_login

        mock_auth_service.initiate_oauth_flow = AsyncMock(
            return_value=("state123", "https://accounts.google.com/oauth?...")
        )
        mock_auth_service.cleanup_expired_oauth_sessions = AsyncMock(return_value=0)

        request = MagicMock()
        request.headers = {}
        request.url = MagicMock()
        request.url.scheme = "https"
        request.url.hostname = "example.com"

        response = await google_login(
            request=request,
            redirect_uri=None,
            db=AsyncMock(),
            auth_service=mock_auth_service,
        )

        assert response.status_code == 302
        assert "google.com" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_google_login_with_custom_redirect(self, mock_auth_service):
        """google_login uses provided redirect_uri."""
        from app.api.admin_auth_routes import google_login

        mock_auth_service.initiate_oauth_flow = AsyncMock(
            return_value=("state123", "https://accounts.google.com/oauth?...")
        )
        mock_auth_service.cleanup_expired_oauth_sessions = AsyncMock(return_value=0)

        request = MagicMock()
        request.headers = {"Host": "example.com", "X-Forwarded-Proto": "https"}
        request.url = MagicMock()
        request.url.scheme = "http"
        request.url.hostname = "localhost"

        await google_login(
            request=request,
            redirect_uri="https://custom.com/callback",
            db=AsyncMock(),
            auth_service=mock_auth_service,
        )

        # Should be called with custom redirect_uri
        mock_auth_service.initiate_oauth_flow.assert_called_once()
        call_args = mock_auth_service.initiate_oauth_flow.call_args
        assert call_args.kwargs["redirect_uri"] == "https://custom.com/callback"

    @pytest.mark.asyncio
    async def test_google_login_error_returns_500(self, mock_auth_service):
        """google_login returns 500 on error."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import google_login

        mock_auth_service.initiate_oauth_flow = AsyncMock(side_effect=Exception("OAuth error"))
        mock_auth_service.cleanup_expired_oauth_sessions = AsyncMock(return_value=0)

        request = MagicMock()
        request.headers = {}
        request.url = MagicMock()
        request.url.scheme = "https"
        request.url.hostname = "example.com"

        with pytest.raises(HTTPException) as exc_info:
            await google_login(
                request=request,
                redirect_uri=None,
                db=AsyncMock(),
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_google_callback_success(self, mock_auth_service):
        """google_callback returns redirect with token cookie."""
        from app.api.admin_auth_routes import google_callback

        mock_auth_service.handle_oauth_callback = AsyncMock(
            return_value={
                "access_token": "jwt_token_123",
                "redirect_uri": "https://example.com/admin",
                "user": {
                    "email": "admin@ciris.ai",
                    "role": "admin",
                },
            }
        )

        response = MagicMock()
        db = AsyncMock()

        result = await google_callback(
            code="auth_code",
            state="state123",
            response=response,
            db=db,
            auth_service=mock_auth_service,
        )

        assert result.status_code == 302
        # Token must NOT leak into the URL — cookie carries auth.
        assert "token=" not in result.headers["location"]
        assert result.headers["location"] == "https://example.com/admin"

    @pytest.mark.asyncio
    async def test_google_callback_invalid_state(self, mock_auth_service):
        """google_callback returns 400 for invalid state."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import google_callback

        mock_auth_service.handle_oauth_callback = AsyncMock(
            side_effect=ValueError("Invalid OAuth state")
        )

        response = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await google_callback(
                code="auth_code",
                state="invalid",
                response=response,
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_google_callback_error_returns_500(self, mock_auth_service):
        """google_callback returns 500 on unexpected error."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import google_callback

        mock_auth_service.handle_oauth_callback = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        response = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await google_callback(
                code="auth_code",
                state="state123",
                response=response,
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self):
        """logout clears admin_token cookie."""
        from app.api.admin_auth_routes import logout

        response = MagicMock()

        result = await logout(response=response)

        response.delete_cookie.assert_called_once_with(key="admin_token", path="/")
        assert result["message"] == "Logged out successfully"

    @pytest.mark.asyncio
    async def test_get_current_user_with_header(self, mock_auth_service):
        """get_current_user works with Authorization header."""
        from app.api.admin_auth_routes import get_current_user

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(uuid4())}

        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = uuid4()
        mock_admin.email = "admin@ciris.ai"
        mock_admin.full_name = "Admin User"
        mock_admin.picture_url = "https://example.com/pic.jpg"
        mock_admin.role = "admin"
        mock_admin.is_active = True
        mock_auth_service.get_admin_user_by_id = AsyncMock(return_value=mock_admin)

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        result = await get_current_user(
            request=request,
            authorization="Bearer test_token",
            db=db,
            auth_service=mock_auth_service,
        )

        assert result["email"] == "admin@ciris.ai"
        assert result["role"] == "admin"

    @pytest.mark.asyncio
    async def test_get_current_user_with_cookie(self, mock_auth_service):
        """get_current_user works with cookie token."""
        from app.api.admin_auth_routes import get_current_user

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(uuid4())}

        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.id = uuid4()
        mock_admin.email = "admin@ciris.ai"
        mock_admin.full_name = "Admin User"
        mock_admin.picture_url = None
        mock_admin.role = "viewer"
        mock_admin.is_active = True
        mock_auth_service.get_admin_user_by_id = AsyncMock(return_value=mock_admin)

        request = MagicMock()
        request.cookies.get.return_value = "cookie_token"
        db = AsyncMock()

        result = await get_current_user(
            request=request,
            authorization=None,
            db=db,
            auth_service=mock_auth_service,
        )

        assert result["email"] == "admin@ciris.ai"

    @pytest.mark.asyncio
    async def test_get_current_user_no_token(self, mock_auth_service):
        """get_current_user raises 401 when no token."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import get_current_user

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                request=request,
                authorization=None,
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, mock_auth_service):
        """get_current_user raises 401 for invalid token."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import get_current_user

        mock_auth_service.verify_jwt_token.return_value = None

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                request=request,
                authorization="Bearer invalid",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_inactive_user(self, mock_auth_service):
        """get_current_user raises 403 for inactive user."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import get_current_user

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(uuid4())}

        mock_admin = MagicMock(spec=AdminUser)
        mock_admin.is_active = False
        mock_auth_service.get_admin_user_by_id = AsyncMock(return_value=mock_admin)

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                request=request,
                authorization="Bearer valid_token",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_current_user_not_found(self, mock_auth_service):
        """get_current_user raises 403 when user not found."""
        from fastapi import HTTPException

        from app.api.admin_auth_routes import get_current_user

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(uuid4())}
        mock_auth_service.get_admin_user_by_id = AsyncMock(return_value=None)

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(
                request=request,
                authorization="Bearer valid_token",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 403
