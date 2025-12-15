"""
Tests for Admin API Routes.

Tests admin endpoints for user management, API keys, analytics, and configuration.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import Account, AdminUser, APIKey, ProviderConfig


class TestAdminDependencies:
    """Tests for admin authentication dependencies."""

    @pytest.fixture
    def mock_auth_service(self):
        """Create mock auth service."""
        service = MagicMock()
        service.verify_jwt_token = MagicMock(return_value={"sub": str(uuid4())})
        service.get_admin_user_by_id = AsyncMock()
        return service

    @pytest.fixture
    def mock_admin_user(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        admin.is_active = True
        return admin

    @pytest.mark.asyncio
    async def test_get_current_admin_with_bearer_token(self, mock_auth_service, mock_admin_user):
        """get_current_admin extracts token from Bearer header."""
        from app.api.admin_dependencies import get_current_admin

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(mock_admin_user.id)}
        mock_auth_service.get_admin_user_by_id.return_value = mock_admin_user

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        result = await get_current_admin(
            request=request,
            authorization="Bearer test_token_123",
            db=db,
            auth_service=mock_auth_service,
        )

        assert result == mock_admin_user
        mock_auth_service.verify_jwt_token.assert_called_once_with("test_token_123")

    @pytest.mark.asyncio
    async def test_get_current_admin_with_cookie(self, mock_auth_service, mock_admin_user):
        """get_current_admin extracts token from cookie if no header."""
        from app.api.admin_dependencies import get_current_admin

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(mock_admin_user.id)}
        mock_auth_service.get_admin_user_by_id.return_value = mock_admin_user

        request = MagicMock()
        request.cookies.get.return_value = "cookie_token_456"
        db = AsyncMock()

        result = await get_current_admin(
            request=request,
            authorization=None,
            db=db,
            auth_service=mock_auth_service,
        )

        assert result == mock_admin_user
        mock_auth_service.verify_jwt_token.assert_called_once_with("cookie_token_456")

    @pytest.mark.asyncio
    async def test_get_current_admin_no_token_raises_401(self, mock_auth_service):
        """get_current_admin raises 401 when no token provided."""
        from fastapi import HTTPException

        from app.api.admin_dependencies import get_current_admin

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                request=request,
                authorization=None,
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 401
        assert "Not authenticated" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_admin_invalid_token_raises_401(self, mock_auth_service):
        """get_current_admin raises 401 when token is invalid."""
        from fastapi import HTTPException

        from app.api.admin_dependencies import get_current_admin

        mock_auth_service.verify_jwt_token.return_value = None

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                request=request,
                authorization="Bearer invalid_token",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 401
        assert "Invalid or expired token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_admin_user_not_found_raises_401(self, mock_auth_service):
        """get_current_admin raises 401 when user not found."""
        from fastapi import HTTPException

        from app.api.admin_dependencies import get_current_admin

        mock_auth_service.verify_jwt_token.return_value = {"sub": str(uuid4())}
        mock_auth_service.get_admin_user_by_id.return_value = None

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                request=request,
                authorization="Bearer valid_token",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 401
        assert "User not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_admin_inactive_user_raises_403(
        self, mock_auth_service, mock_admin_user
    ):
        """get_current_admin raises 403 when user is inactive."""
        from fastapi import HTTPException

        from app.api.admin_dependencies import get_current_admin

        mock_admin_user.is_active = False
        mock_auth_service.verify_jwt_token.return_value = {"sub": str(mock_admin_user.id)}
        mock_auth_service.get_admin_user_by_id.return_value = mock_admin_user

        request = MagicMock()
        request.cookies.get.return_value = None
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_admin(
                request=request,
                authorization="Bearer valid_token",
                db=db,
                auth_service=mock_auth_service,
            )

        assert exc_info.value.status_code == 403
        assert "deactivated" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_require_admin_role_allows_admin(self, mock_admin_user):
        """require_admin_role allows users with admin role."""
        from app.api.admin_dependencies import require_admin_role

        mock_admin_user.role = "admin"
        result = await require_admin_role(admin=mock_admin_user)
        assert result == mock_admin_user

    @pytest.mark.asyncio
    async def test_require_admin_role_rejects_viewer(self, mock_admin_user):
        """require_admin_role rejects users with viewer role."""
        from fastapi import HTTPException

        from app.api.admin_dependencies import require_admin_role

        mock_admin_user.role = "viewer"

        with pytest.raises(HTTPException) as exc_info:
            await require_admin_role(admin=mock_admin_user)

        assert exc_info.value.status_code == 403
        assert "Admin role required" in exc_info.value.detail


class TestUserManagement:
    """Tests for user management endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.fixture
    def mock_account(self):
        """Create mock billing account."""
        account = MagicMock(spec=Account)
        account.id = uuid4()
        account.oauth_provider = "oauth:google"
        account.external_id = "user@example.com"
        account.wa_id = None
        account.tenant_id = None
        account.customer_email = "user@example.com"
        account.balance_minor = 1000
        account.paid_credits = 10
        account.free_uses_remaining = 5
        account.total_uses = 100
        account.currency = "USD"
        account.plan_name = "free"
        account.status = "active"
        account.created_at = datetime.now(UTC)
        return account

    @pytest.mark.asyncio
    async def test_list_users_returns_paginated_results(self, mock_admin, mock_account):
        """list_users returns paginated user list."""
        from app.api.admin_routes import list_users

        db = AsyncMock()

        # Mock count query
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        # Mock accounts query
        accounts_result = MagicMock()
        accounts_result.scalars.return_value.all.return_value = [mock_account]

        # Mock charge stats
        charge_row = MagicMock()
        charge_row.charge_count = 5
        charge_row.total_charged = 500
        charge_row.last_charge_at = datetime.now(UTC)
        charge_result = MagicMock()
        charge_result.one.return_value = charge_row

        # Mock credit stats
        credit_row = MagicMock()
        credit_row.credit_count = 2
        credit_row.total_credited = 1000
        credit_row.last_credit_at = datetime.now(UTC)
        credit_result = MagicMock()
        credit_result.one.return_value = credit_row

        db.execute = AsyncMock(
            side_effect=[count_result, accounts_result, charge_result, credit_result]
        )

        result = await list_users(
            page=1,
            page_size=50,
            status_filter=None,
            search=None,
            db=db,
            admin=mock_admin,
        )

        assert result.total == 1
        assert result.page == 1
        assert len(result.users) == 1
        assert result.users[0].account_id == mock_account.id

    @pytest.mark.asyncio
    async def test_get_user_returns_user_details(self, mock_admin, mock_account):
        """get_user returns detailed user information."""
        from app.api.admin_routes import get_user

        db = AsyncMock()

        # Mock account query
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        # Mock charge stats
        charge_row = MagicMock()
        charge_row.charge_count = 5
        charge_row.total_charged = 500
        charge_row.last_charge_at = datetime.now(UTC)
        charge_result = MagicMock()
        charge_result.one.return_value = charge_row

        # Mock credit stats
        credit_row = MagicMock()
        credit_row.credit_count = 2
        credit_row.total_credited = 1000
        credit_row.last_credit_at = datetime.now(UTC)
        credit_result = MagicMock()
        credit_result.one.return_value = credit_row

        db.execute = AsyncMock(side_effect=[account_result, charge_result, credit_result])

        result = await get_user(
            account_id=mock_account.id,
            db=db,
            admin=mock_admin,
        )

        assert result.account_id == mock_account.id
        assert result.customer_email == mock_account.customer_email
        assert result.charge_count == 5
        assert result.credit_count == 2

    @pytest.mark.asyncio
    async def test_get_user_not_found_raises_404(self, mock_admin):
        """get_user raises 404 when user not found."""
        from fastapi import HTTPException

        from app.api.admin_routes import get_user

        db = AsyncMock()
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=account_result)

        with pytest.raises(HTTPException) as exc_info:
            await get_user(
                account_id=uuid4(),
                db=db,
                admin=mock_admin,
            )

        assert exc_info.value.status_code == 404


class TestAPIKeyManagement:
    """Tests for API key management endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.fixture
    def mock_api_key(self):
        """Create mock API key."""
        key = MagicMock(spec=APIKey)
        key.id = uuid4()
        key.name = "Test Key"
        key.key_prefix = "crs_test_"
        key.environment = "test"
        key.permissions = ["billing:read", "billing:write"]
        key.status = "active"
        key.created_at = datetime.now(UTC)
        key.expires_at = None
        key.last_used_at = None
        key.created_by = MagicMock(email="admin@ciris.ai")
        return key

    @pytest.mark.asyncio
    async def test_list_api_keys_returns_all_keys(self, mock_admin, mock_api_key):
        """list_api_keys returns list of API keys."""
        from app.api.admin_routes import list_api_keys

        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [mock_api_key]
        db.execute = AsyncMock(return_value=result)

        keys = await list_api_keys(
            environment=None,
            status_filter=None,
            db=db,
            admin=mock_admin,
        )

        assert len(keys) == 1
        assert keys[0].id == mock_api_key.id
        assert keys[0].name == "Test Key"

    @pytest.mark.asyncio
    async def test_list_api_keys_filters_by_environment(self, mock_admin, mock_api_key):
        """list_api_keys filters by environment."""
        from app.api.admin_routes import list_api_keys

        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [mock_api_key]
        db.execute = AsyncMock(return_value=result)

        await list_api_keys(
            environment="test",
            status_filter=None,
            db=db,
            admin=mock_admin,
        )

        # Verify query was executed
        db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_api_key_success(self, mock_admin):
        """create_api_key creates new API key."""
        from app.api.admin_routes import APIKeyCreateRequest, create_api_key

        db = AsyncMock()

        mock_key_data = MagicMock()
        mock_key_data.key_id = uuid4()
        mock_key_data.name = "New Key"
        mock_key_data.key_prefix = "crs_live_"
        mock_key_data.plaintext_key = "crs_live_abc123"
        mock_key_data.environment = "live"
        mock_key_data.permissions = ["billing:read"]
        mock_key_data.expires_at = None
        mock_key_data.created_at = datetime.now(UTC)

        request = APIKeyCreateRequest(
            name="New Key",
            environment="live",
            permissions=["billing:read"],
        )

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.create_api_key = AsyncMock(return_value=mock_key_data)

            result = await create_api_key(
                request=request,
                db=db,
                admin=mock_admin,
            )

        assert result.name == "New Key"
        assert result.plaintext_key == "crs_live_abc123"

    @pytest.mark.asyncio
    async def test_create_api_key_invalid_raises_400(self, mock_admin):
        """create_api_key raises 400 on invalid request."""
        from fastapi import HTTPException

        from app.api.admin_routes import APIKeyCreateRequest, create_api_key

        db = AsyncMock()

        request = APIKeyCreateRequest(
            name="Test",
            environment="live",
        )

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.create_api_key = AsyncMock(side_effect=ValueError("Invalid name"))

            with pytest.raises(HTTPException) as exc_info:
                await create_api_key(
                    request=request,
                    db=db,
                    admin=mock_admin,
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_revoke_api_key_success(self, mock_admin):
        """revoke_api_key revokes an API key."""
        from app.api.admin_routes import revoke_api_key

        db = AsyncMock()
        key_id = uuid4()

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.revoke_api_key = AsyncMock()

            await revoke_api_key(
                key_id=key_id,
                db=db,
                admin=mock_admin,
            )

            mock_service.revoke_api_key.assert_called_once_with(key_id)

    @pytest.mark.asyncio
    async def test_revoke_api_key_not_found_raises_404(self, mock_admin):
        """revoke_api_key raises 404 when key not found."""
        from fastapi import HTTPException

        from app.api.admin_routes import revoke_api_key

        db = AsyncMock()
        key_id = uuid4()

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.revoke_api_key = AsyncMock(side_effect=ValueError("Key not found"))

            with pytest.raises(HTTPException) as exc_info:
                await revoke_api_key(
                    key_id=key_id,
                    db=db,
                    admin=mock_admin,
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_api_key_success(self, mock_admin):
        """rotate_api_key rotates an API key."""
        from app.api.admin_routes import rotate_api_key

        db = AsyncMock()
        key_id = uuid4()

        mock_rotation_data = MagicMock()
        mock_rotation_data.key_id = uuid4()
        mock_rotation_data.name = "Rotated Key"
        mock_rotation_data.key_prefix = "crs_live_"
        mock_rotation_data.plaintext_key = "crs_live_new_key_xyz"

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.rotate_api_key = AsyncMock(return_value=mock_rotation_data)

            result = await rotate_api_key(
                key_id=key_id,
                db=db,
                admin=mock_admin,
            )

        assert result.new_plaintext_key == "crs_live_new_key_xyz"
        assert result.old_key_expires_at is not None

    @pytest.mark.asyncio
    async def test_rotate_api_key_invalid_raises_400(self, mock_admin):
        """rotate_api_key raises 400 on invalid key."""
        from fastapi import HTTPException

        from app.api.admin_routes import rotate_api_key

        db = AsyncMock()

        with patch("app.api.admin_routes.APIKeyService") as MockService:
            mock_service = MockService.return_value
            mock_service.rotate_api_key = AsyncMock(side_effect=ValueError("Cannot rotate"))

            with pytest.raises(HTTPException) as exc_info:
                await rotate_api_key(
                    key_id=uuid4(),
                    db=db,
                    admin=mock_admin,
                )

            assert exc_info.value.status_code == 400


class TestTokenRevocation:
    """Tests for token revocation endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.mark.asyncio
    async def test_revoke_user_token_with_raw_token(self, mock_admin):
        """revoke_user_token revokes token using raw token."""
        from app.api.admin_routes import RevokeTokenRequest, revoke_user_token

        db = AsyncMock()
        db.merge = AsyncMock()
        db.commit = AsyncMock()

        request = RevokeTokenRequest(
            user_id="google_sub_123",
            reason="Security incident",
            token="raw_jwt_token_here",
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        with patch("app.api.admin_routes.token_revocation_service") as mock_service:
            mock_service.hash_token.return_value = "a" * 64
            mock_service.revoke_token = AsyncMock()

            result = await revoke_user_token(
                request=request,
                db=db,
                admin=mock_admin,
            )

        assert result.user_id == "google_sub_123"
        assert result.reason == "Security incident"
        assert result.revoked_by == mock_admin.email
        mock_service.revoke_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_user_token_with_hash(self, mock_admin):
        """revoke_user_token revokes token using hash directly."""
        from app.api.admin_routes import RevokeTokenRequest, revoke_user_token

        db = AsyncMock()
        db.merge = AsyncMock()
        db.commit = AsyncMock()

        token_hash = "a" * 64
        request = RevokeTokenRequest(
            user_id="google_sub_123",
            reason="Token compromised",
            token_hash=token_hash,
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        result = await revoke_user_token(
            request=request,
            db=db,
            admin=mock_admin,
        )

        assert result.token_hash_prefix == token_hash[:16]
        db.merge.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_user_token_no_token_raises_400(self, mock_admin):
        """revoke_user_token raises 400 when neither token nor hash provided."""
        from fastapi import HTTPException

        from app.api.admin_routes import RevokeTokenRequest, revoke_user_token

        db = AsyncMock()

        request = RevokeTokenRequest(
            user_id="google_sub_123",
            reason="Test",
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        with pytest.raises(HTTPException) as exc_info:
            await revoke_user_token(
                request=request,
                db=db,
                admin=mock_admin,
            )

        assert exc_info.value.status_code == 400
        assert "Either 'token' or 'token_hash'" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_revocation_stats(self, mock_admin):
        """get_revocation_stats returns cache statistics."""
        from app.api.admin_routes import get_revocation_stats

        db = AsyncMock()

        with patch("app.api.admin_routes.token_revocation_service") as mock_service:
            mock_service.get_revocation_stats = AsyncMock(
                return_value={
                    "cache_size": 10,
                    "active_revocations": 5,
                    "cache_loaded": True,
                }
            )

            result = await get_revocation_stats(db=db, admin=mock_admin)

        assert result.cache_size == 10
        assert result.active_revocations == 5
        assert result.cache_loaded is True


class TestAnalytics:
    """Tests for analytics endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.mark.asyncio
    async def test_get_analytics_overview(self, mock_admin):
        """get_analytics_overview returns dashboard stats."""
        from app.api.admin_routes import get_analytics_overview

        db = AsyncMock()

        # Mock all the count queries
        async def mock_execute(stmt):
            result = MagicMock()
            result.scalar_one.return_value = 100
            return result

        db.execute = AsyncMock(side_effect=mock_execute)

        result = await get_analytics_overview(db=db, admin=mock_admin)

        assert result.total_users == 100
        assert result.active_users == 100
        assert result.total_api_keys == 100

    @pytest.mark.asyncio
    async def test_get_daily_analytics(self, mock_admin):
        """get_daily_analytics returns daily aggregated data."""
        from app.api.admin_routes import get_daily_analytics

        db = AsyncMock()

        # Mock charge aggregates
        charge_result = MagicMock()
        charge_result.all.return_value = []

        # Mock credit aggregates
        credit_result = MagicMock()
        credit_result.all.return_value = []

        db.execute = AsyncMock(side_effect=[charge_result, credit_result])

        result = await get_daily_analytics(days=7, db=db, admin=mock_admin)

        # Result should be a list of daily data
        assert isinstance(result, list)


class TestProviderConfig:
    """Tests for provider configuration endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.fixture
    def mock_config(self):
        """Create mock provider config."""
        config = MagicMock(spec=ProviderConfig)
        config.id = uuid4()
        config.provider_type = "stripe"
        config.is_active = True
        config.config_data = {"api_key": "sk_test_xxx"}
        config.updated_at = datetime.now(UTC)
        return config

    @pytest.mark.asyncio
    async def test_list_provider_configs(self, mock_admin, mock_config):
        """list_provider_configs returns all configurations."""
        from app.api.admin_routes import list_provider_configs

        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [mock_config]
        db.execute = AsyncMock(return_value=result)

        configs = await list_provider_configs(db=db, admin=mock_admin)

        assert len(configs) == 1
        assert configs[0].provider_name == "stripe"
        assert configs[0].is_enabled is True

    @pytest.mark.asyncio
    async def test_update_provider_config_existing(self, mock_admin, mock_config):
        """update_provider_config updates existing configuration."""
        from app.api.admin_routes import ProviderConfigUpdateRequest, update_provider_config

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_config
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        request = ProviderConfigUpdateRequest(
            is_enabled=False,
            config_data={"api_key": "sk_live_xxx"},
        )

        updated = await update_provider_config(
            provider_name="stripe",
            request=request,
            db=db,
            admin=mock_admin,
        )

        assert updated.provider_name == "stripe"
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_provider_config_creates_new(self, mock_admin):
        """update_provider_config creates new configuration if not exists."""
        from app.api.admin_routes import ProviderConfigUpdateRequest, update_provider_config

        db = AsyncMock()

        # First query returns None (config not found)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        db.add = MagicMock()
        db.commit = AsyncMock()

        # Mock refresh to update the mock config
        mock_new_config = MagicMock()
        mock_new_config.id = uuid4()
        mock_new_config.provider_type = "new_provider"
        mock_new_config.is_active = True
        mock_new_config.config_data = {}
        mock_new_config.updated_at = datetime.now(UTC)

        async def mock_refresh(obj):
            obj.id = mock_new_config.id
            obj.provider_type = "new_provider"
            obj.is_active = True
            obj.config_data = {}
            obj.updated_at = mock_new_config.updated_at

        db.refresh = mock_refresh

        request = ProviderConfigUpdateRequest(
            is_enabled=True,
        )

        updated = await update_provider_config(
            provider_name="new_provider",
            request=request,
            db=db,
            admin=mock_admin,
        )

        db.add.assert_called_once()
        assert updated.provider_name == "new_provider"


class TestMarginAnalytics:
    """Tests for margin analytics endpoints."""

    @pytest.fixture
    def mock_admin(self):
        """Create mock admin user."""
        admin = MagicMock(spec=AdminUser)
        admin.id = uuid4()
        admin.email = "admin@ciris.ai"
        admin.role = "admin"
        return admin

    @pytest.mark.asyncio
    async def test_get_margin_overview(self, mock_admin):
        """get_margin_overview returns margin statistics."""
        from app.api.admin_routes import get_margin_overview

        db = AsyncMock()

        # Mock usage stats query
        usage_row = MagicMock()
        usage_row.total_interactions = 100
        usage_row.total_cost = 5000  # $50
        usage_row.total_llm_calls = 200
        usage_row.total_prompt_tokens = 50000
        usage_row.total_completion_tokens = 25000
        usage_row.total_errors = 5
        usage_row.total_fallbacks = 2
        usage_row.unique_users = 10

        usage_result = MagicMock()
        usage_result.one.return_value = usage_row

        # Mock model usage query
        model_result = MagicMock()
        model_result.all.return_value = [
            MagicMock(model="gpt-4", count=50),
            MagicMock(model="gpt-3.5-turbo", count=150),
        ]

        db.execute = AsyncMock(side_effect=[usage_result, model_result])

        result = await get_margin_overview(days=30, db=db, admin=mock_admin)

        assert result.total_interactions == 100
        # Revenue = 100 interactions * 100 cents = $100
        assert result.total_revenue_cents == 10000
        assert result.total_llm_cost_cents == 5000
        # Margin = $100 - $50 = $50
        assert result.total_margin_cents == 5000

    @pytest.mark.asyncio
    async def test_get_daily_margin(self, mock_admin):
        """get_daily_margin returns daily margin breakdown."""
        from app.api.admin_routes import get_daily_margin

        db = AsyncMock()

        # Mock daily stats
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        row = MagicMock()
        row.date = today
        row.total_interactions = 10
        row.total_cost = 500
        row.unique_users = 3
        row.total_llm_calls = 20
        row.prompt_tokens = 5000
        row.completion_tokens = 2500
        row.error_count = 0
        row.fallback_count = 0

        result = MagicMock()
        result.all.return_value = [row]
        db.execute = AsyncMock(return_value=result)

        daily = await get_daily_margin(days=7, db=db, admin=mock_admin)

        assert len(daily) > 0
        assert daily[0].total_interactions == 10
        assert daily[0].total_revenue_cents == 1000  # 10 * 100
        assert daily[0].total_llm_cost_cents == 500

    @pytest.mark.asyncio
    async def test_get_user_margin_detail_not_found(self, mock_admin):
        """get_user_margin_detail raises 404 when account not found."""
        from fastapi import HTTPException

        from app.api.admin_routes import get_user_margin_detail

        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)

        with pytest.raises(HTTPException) as exc_info:
            await get_user_margin_detail(
                account_id=uuid4(),
                days=30,
                db=db,
                admin=mock_admin,
            )

        assert exc_info.value.status_code == 404
