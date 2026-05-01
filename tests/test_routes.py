"""
Tests for API Routes.

Comprehensive tests for billing API endpoint functions.
Tests route handler functions directly with mocked dependencies.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.dependencies import CombinedAuth, UserIdentity
from app.api.routes import _resolve_identity_from_auth
from app.exceptions import (
    AccountClosedError,
    AccountNotFoundError,
    AccountSuspendedError,
    DataIntegrityError,
    IdempotencyConflictError,
    InsufficientCreditsError,
    WriteVerificationError,
)
from app.models.api import (
    AccountStatus,
    AddCreditsRequest,
    ChargeMetadata,
    CreateAccountRequest,
    CreateChargeRequest,
    CreditCheckRequest,
)
from app.models.domain import AccountData, AccountIdentity, ChargeData, CreditData
from app.services.api_key import APIKeyData

# ============================================================================
# Helper Function Tests
# ============================================================================


class TestResolveIdentityFromAuth:
    """Tests for _resolve_identity_from_auth helper."""

    def test_jwt_auth_uses_token_identity(self, jwt_auth: CombinedAuth):
        """JWT auth uses identity from token, ignoring request values."""
        result = _resolve_identity_from_auth(
            auth=jwt_auth,
            request_oauth="oauth:discord",
            request_external_id="discord-user",
            request_wa_id="wa-123",
            request_tenant_id="tenant-123",
        )

        assert result.oauth_provider == "oauth:google"
        assert result.external_id == "user@example.com"
        assert result.wa_id == "wa-123"  # From request
        assert result.tenant_id == "tenant-123"  # From request

    def test_api_key_auth_uses_request_identity(self, api_key_auth: CombinedAuth):
        """API key auth uses identity from request."""
        result = _resolve_identity_from_auth(
            auth=api_key_auth,
            request_oauth="oauth:discord",
            request_external_id="discord-user",
            request_wa_id="wa-123",
            request_tenant_id="tenant-123",
        )

        assert result.oauth_provider == "oauth:discord"
        assert result.external_id == "discord-user"
        assert result.wa_id == "wa-123"
        assert result.tenant_id == "tenant-123"

    def test_request_with_none_optional_fields(self, api_key_auth: CombinedAuth):
        """Optional fields can be None."""
        result = _resolve_identity_from_auth(
            auth=api_key_auth,
            request_oauth="oauth:google",
            request_external_id="test@example.com",
        )

        assert result.wa_id is None
        assert result.tenant_id is None


# ============================================================================
# Credit Check Route Tests
# ============================================================================


class TestCheckCreditRoute:
    """Tests for check_credit route function."""

    @pytest.mark.asyncio
    async def test_check_credit_with_api_key_auth(
        self,
        db_session: AsyncMock,
        api_key_auth: CombinedAuth,
    ):
        """Credit check with API key auth calls billing service."""
        from app.api.routes import check_credit
        from app.models.api import CreditCheckResponse

        request = CreditCheckRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            context={},
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.check_credit = AsyncMock(
                return_value=CreditCheckResponse(
                    has_credit=True,
                    credits_remaining=100,
                    plan_name="free",
                    free_uses_remaining=5,
                    daily_free_uses_remaining=2,
                    reason=None,
                )
            )
            service.update_account_metadata = AsyncMock()

            result = await check_credit(request, db_session, api_key_auth)

            assert result.has_credit is True
            assert result.credits_remaining == 100
            service.check_credit.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_credit_with_jwt_auth(
        self,
        db_session: AsyncMock,
        jwt_auth: CombinedAuth,
    ):
        """Credit check with JWT auth uses token identity."""
        from app.api.routes import check_credit
        from app.models.api import CreditCheckResponse

        request = CreditCheckRequest(
            oauth_provider="oauth:discord",  # Should be ignored
            external_id="discord-user",  # Should be ignored
            context={},
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.check_credit = AsyncMock(
                return_value=CreditCheckResponse(
                    has_credit=True,
                    credits_remaining=100,
                    plan_name="free",
                    free_uses_remaining=5,
                    daily_free_uses_remaining=2,
                    reason=None,
                )
            )
            service.update_account_metadata = AsyncMock()

            await check_credit(request, db_session, jwt_auth)

            # Verify the identity used was from JWT, not request
            call_args = service.check_credit.call_args
            identity = call_args[0][0]
            assert identity.oauth_provider == "oauth:google"
            assert identity.external_id == "user@example.com"

    @pytest.mark.asyncio
    async def test_check_credit_missing_permission_raises(
        self,
        db_session: AsyncMock,
    ):
        """Credit check with API key missing permission raises HTTPException."""
        from fastapi import HTTPException

        from app.api.routes import check_credit

        # API key without billing:read
        api_key = APIKeyData(
            key_id=uuid4(),
            name="No Perms Key",
            key_prefix="cbk_test",
            environment="test",
            permissions=[],  # No permissions
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )
        auth = CombinedAuth(auth_type="api_key", api_key=api_key, user=None)

        request = CreditCheckRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            context={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_credit(request, db_session, auth)

        assert exc_info.value.status_code == 403
        assert "billing:read" in exc_info.value.detail


# ============================================================================
# Create Charge Route Tests
# ============================================================================


class TestCreateChargeRoute:
    """Tests for create_charge route function."""

    @pytest.mark.asyncio
    async def test_create_charge_success(
        self,
        db_session: AsyncMock,
        active_account: MagicMock,
    ):
        """Successfully create a charge."""
        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
        )

        charge_id = uuid4()
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(
                return_value=ChargeData(
                    charge_id=charge_id,
                    account_id=active_account.id,
                    amount_minor=100,
                    currency="USD",
                    balance_before=1000,
                    balance_after=900,
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    created_at=datetime.now(UTC),
                )
            )

            result = await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert result.charge_id == charge_id
            assert result.amount_minor == 100
            assert result.balance_after == 900
            service.create_charge.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_charge_account_not_found(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 404 when account not found."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="unknown@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(
                side_effect=AccountNotFoundError(
                    AccountIdentity(
                        oauth_provider="oauth:google",
                        external_id="unknown@example.com",
                        wa_id=None,
                        tenant_id=None,
                    )
                )
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_charge_insufficient_credits(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 402 when insufficient credits."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=1000,
            currency="USD",
            description="Test charge",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(
                side_effect=InsufficientCreditsError(balance=50, required=1000)
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 402
            assert "50" in exc_info.value.detail
            assert "1000" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_charge_account_suspended(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 403 when account suspended."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(
                side_effect=AccountSuspendedError(uuid4(), "Payment failed")
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 403
            assert "suspended" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_charge_account_closed(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 403 when account closed."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(side_effect=AccountClosedError(uuid4()))

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 403
            assert "closed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_charge_idempotency_conflict(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 409 on idempotency conflict."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
            idempotency_key="duplicate-key",
        )

        existing_id = uuid4()
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(side_effect=IdempotencyConflictError(existing_id))

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 409
            assert exc_info.value.headers["X-Existing-Charge-ID"] == str(existing_id)

    @pytest.mark.asyncio
    async def test_create_charge_write_verification_error(
        self,
        db_session: AsyncMock,
    ):
        """Create charge raises 500 on write verification error."""
        from fastapi import HTTPException

        from app.api.routes import create_charge

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

        request = CreateChargeRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=100,
            currency="USD",
            description="Test charge",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.create_charge = AsyncMock(
                side_effect=WriteVerificationError("Charge not found after insert")
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_charge(
                request, db_session, CombinedAuth(auth_type="api_key", api_key=api_key)
            )

            assert exc_info.value.status_code == 500


# ============================================================================
# Add Credits Route Tests
# ============================================================================


class TestAddCreditsRoute:
    """Tests for add_credits route function."""

    @pytest.mark.asyncio
    async def test_add_credits_success(
        self,
        db_session: AsyncMock,
        active_account: MagicMock,
    ):
        """Successfully add credits."""
        from app.api.routes import add_credits

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

        request = AddCreditsRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=500,
            currency="USD",
            description="Test credit",
            transaction_type="grant",
        )

        credit_id = uuid4()
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.add_credits = AsyncMock(
                return_value=CreditData(
                    credit_id=credit_id,
                    account_id=active_account.id,
                    amount_minor=500,
                    currency="USD",
                    balance_before=1000,
                    balance_after=1500,
                    transaction_type="grant",
                    description="Test credit",
                    external_transaction_id=None,
                    created_at=datetime.now(UTC),
                )
            )

            result = await add_credits(request, db_session, api_key)

            assert result.credit_id == credit_id
            assert result.amount_minor == 500
            assert result.balance_after == 1500

    @pytest.mark.asyncio
    async def test_add_credits_account_not_found(
        self,
        db_session: AsyncMock,
    ):
        """Add credits raises 404 when account not found."""
        from fastapi import HTTPException

        from app.api.routes import add_credits

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

        request = AddCreditsRequest(
            oauth_provider="oauth:google",
            external_id="unknown@example.com",
            amount_minor=500,
            currency="USD",
            description="Test credit",
            transaction_type="grant",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.add_credits = AsyncMock(
                side_effect=AccountNotFoundError(
                    AccountIdentity(
                        oauth_provider="oauth:google",
                        external_id="unknown@example.com",
                        wa_id=None,
                        tenant_id=None,
                    )
                )
            )

            with pytest.raises(HTTPException) as exc_info:
                await add_credits(request, db_session, api_key)

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_credits_idempotency_conflict(
        self,
        db_session: AsyncMock,
    ):
        """Add credits raises 409 on idempotency conflict."""
        from fastapi import HTTPException

        from app.api.routes import add_credits

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

        request = AddCreditsRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=500,
            currency="USD",
            description="Test credit",
            transaction_type="grant",
            idempotency_key="duplicate-key",
        )

        existing_id = uuid4()
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.add_credits = AsyncMock(side_effect=IdempotencyConflictError(existing_id))

            with pytest.raises(HTTPException) as exc_info:
                await add_credits(request, db_session, api_key)

            assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_add_credits_data_integrity_error(
        self,
        db_session: AsyncMock,
    ):
        """Add credits raises 400 on data integrity error."""
        from fastapi import HTTPException

        from app.api.routes import add_credits

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

        request = AddCreditsRequest(
            oauth_provider="oauth:google",
            external_id="test@example.com",
            amount_minor=500,
            currency="EUR",  # Wrong currency
            description="Test credit",
            transaction_type="grant",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.update_account_metadata = AsyncMock()
            service.add_credits = AsyncMock(
                side_effect=DataIntegrityError("Currency mismatch: EUR vs USD")
            )

            with pytest.raises(HTTPException) as exc_info:
                await add_credits(request, db_session, api_key)

            assert exc_info.value.status_code == 400


# ============================================================================
# Account Route Tests
# ============================================================================


class TestAccountRoutes:
    """Tests for account endpoints."""

    @pytest.mark.asyncio
    async def test_create_account_success(
        self,
        db_session: AsyncMock,
    ):
        """Successfully create an account."""
        from app.api.routes import create_or_update_account

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

        request = CreateAccountRequest(
            oauth_provider="oauth:google",
            external_id="newuser@example.com",
            initial_balance_minor=0,
            currency="USD",
            plan_name="free",
        )

        account_id = uuid4()
        now = datetime.now(UTC)
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_or_create_account = AsyncMock(
                return_value=AccountData(
                    account_id=account_id,
                    oauth_provider="oauth:google",
                    external_id="newuser@example.com",
                    wa_id=None,
                    tenant_id=None,
                    customer_email=None,
                    balance_minor=0,
                    currency="USD",
                    plan_name="free",
                    status=AccountStatus.ACTIVE,
                    paid_credits=0,
                    marketing_opt_in=False,
                    marketing_opt_in_at=None,
                    marketing_opt_in_source=None,
                    created_at=now,
                    updated_at=now,
                    free_uses_remaining=10,
                    daily_free_uses_remaining=2,
                    daily_free_uses_limit=2,
                    daily_free_uses_reset_at=None,
                )
            )

            result = await create_or_update_account(request, db_session, api_key)

            assert result.account_id == account_id
            assert result.oauth_provider == "oauth:google"
            assert result.status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_create_account_write_verification_error(
        self,
        db_session: AsyncMock,
    ):
        """Create account raises 500 on write verification error."""
        from fastapi import HTTPException

        from app.api.routes import create_or_update_account

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

        request = CreateAccountRequest(
            oauth_provider="oauth:google",
            external_id="newuser@example.com",
            initial_balance_minor=0,
            currency="USD",
            plan_name="free",
        )

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_or_create_account = AsyncMock(
                side_effect=WriteVerificationError("Account not found after insert")
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_or_update_account(request, db_session, api_key)

            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_account_success(
        self,
        db_session: AsyncMock,
    ):
        """Successfully get an account."""
        from app.api.routes import get_account

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

        account_id = uuid4()
        now = datetime.now(UTC)
        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_account = AsyncMock(
                return_value=AccountData(
                    account_id=account_id,
                    oauth_provider="oauth:google",
                    external_id="test@example.com",
                    wa_id=None,
                    tenant_id=None,
                    customer_email="test@example.com",
                    balance_minor=1000,
                    currency="USD",
                    plan_name="free",
                    status=AccountStatus.ACTIVE,
                    paid_credits=100,
                    marketing_opt_in=False,
                    marketing_opt_in_at=None,
                    marketing_opt_in_source=None,
                    created_at=now,
                    updated_at=now,
                    free_uses_remaining=5,
                    daily_free_uses_remaining=2,
                    daily_free_uses_limit=2,
                    daily_free_uses_reset_at=None,
                )
            )

            result = await get_account(
                oauth_provider="oauth:google",
                external_id="test@example.com",
                wa_id=None,
                tenant_id=None,
                db=db_session,
                api_key=api_key,
            )

            assert result.account_id == account_id
            assert result.paid_credits == 100

    @pytest.mark.asyncio
    async def test_get_account_not_found(
        self,
        db_session: AsyncMock,
    ):
        """Get account raises 404 when not found."""
        from fastapi import HTTPException

        from app.api.routes import get_account

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

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_account = AsyncMock(
                side_effect=AccountNotFoundError(
                    AccountIdentity(
                        oauth_provider="oauth:google",
                        external_id="unknown@example.com",
                        wa_id=None,
                        tenant_id=None,
                    )
                )
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_account(
                    oauth_provider="oauth:google",
                    external_id="unknown@example.com",
                    wa_id=None,
                    tenant_id=None,
                    db=db_session,
                    api_key=api_key,
                )

            assert exc_info.value.status_code == 404


# ============================================================================
# Health Check Route Tests
# ============================================================================


class TestHealthCheckRoute:
    """Tests for health_check route function."""

    @pytest.mark.asyncio
    async def test_health_check_success(
        self,
        db_session: AsyncMock,
    ):
        """Health check returns healthy when database connected."""
        from app.api.routes import health_check

        mock_result = MagicMock()
        db_session.execute = AsyncMock(return_value=mock_result)

        result = await health_check(db_session)

        assert result.status == "healthy"
        assert result.database == "connected"
        assert result.timestamp is not None

    @pytest.mark.asyncio
    async def test_health_check_database_error(
        self,
        db_session: AsyncMock,
    ):
        """Health check raises 503 when database fails."""
        from fastapi import HTTPException

        from app.api.routes import health_check

        db_session.execute = AsyncMock(side_effect=Exception("Connection refused"))

        with pytest.raises(HTTPException) as exc_info:
            await health_check(db_session)

        assert exc_info.value.status_code == 503


# ============================================================================
# Transactions Route Tests
# ============================================================================


class TestListTransactionsRoute:
    """Tests for list_transactions route function."""

    @pytest.mark.asyncio
    async def test_list_transactions_account_not_found(
        self,
        db_session: AsyncMock,
    ):
        """List transactions returns empty for nonexistent account."""
        from app.api.routes import list_transactions

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

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_account = AsyncMock(
                side_effect=AccountNotFoundError(
                    AccountIdentity(
                        oauth_provider="oauth:google",
                        external_id="unknown@example.com",
                        wa_id=None,
                        tenant_id=None,
                    )
                )
            )

            result = await list_transactions(
                oauth_provider="oauth:google",
                external_id="unknown@example.com",
                wa_id=None,
                tenant_id=None,
                limit=50,
                offset=0,
                db=db_session,
                api_key=api_key,
            )

            assert result.transactions == []
            assert result.total_count == 0
            assert result.has_more is False

    @pytest.mark.asyncio
    async def test_list_transactions_success(
        self,
        db_session: AsyncMock,
        active_account: MagicMock,
    ):
        """List transactions returns charges and credits."""
        from app.api.routes import list_transactions

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

        # Create mock charge and credit results
        now = datetime.now(UTC)
        mock_charge = MagicMock()
        mock_charge.transaction_id = uuid4()
        mock_charge.amount_minor = 100
        mock_charge.currency = "USD"
        mock_charge.description = "Test charge"
        mock_charge.created_at = now
        mock_charge.balance_after = 900
        mock_charge.metadata_message_id = None
        mock_charge.metadata_agent_id = None
        mock_charge.metadata_channel_id = None
        mock_charge.metadata_request_id = None

        mock_credit = MagicMock()
        mock_credit.transaction_id = uuid4()
        mock_credit.amount_minor = 500
        mock_credit.currency = "USD"
        mock_credit.description = "Test credit"
        mock_credit.created_at = now
        mock_credit.balance_after = 1500
        mock_credit.transaction_type = "grant"
        mock_credit.external_transaction_id = None

        with patch("app.api.routes.BillingService") as MockService:
            service = MockService.return_value
            service.get_account = AsyncMock(
                return_value=AccountData(
                    account_id=active_account.id,
                    oauth_provider="oauth:google",
                    external_id="test@example.com",
                    wa_id=None,
                    tenant_id=None,
                    customer_email=None,
                    balance_minor=1000,
                    currency="USD",
                    plan_name="free",
                    status=AccountStatus.ACTIVE,
                    paid_credits=100,
                    marketing_opt_in=False,
                    marketing_opt_in_at=None,
                    marketing_opt_in_source=None,
                    created_at=now,
                    updated_at=now,
                    free_uses_remaining=5,
                    daily_free_uses_remaining=2,
                    daily_free_uses_limit=2,
                    daily_free_uses_reset_at=None,
                )
            )

            # Mock database queries for charges and credits
            charges_result = MagicMock()
            charges_result.all = MagicMock(return_value=[mock_charge])
            credits_result = MagicMock()
            credits_result.all = MagicMock(return_value=[mock_credit])

            # First call is for charges, second for credits
            db_session.execute = AsyncMock(
                side_effect=[
                    MagicMock(all=MagicMock(return_value=[mock_charge])),
                    MagicMock(all=MagicMock(return_value=[mock_credit])),
                ]
            )

            result = await list_transactions(
                oauth_provider="oauth:google",
                external_id="test@example.com",
                wa_id=None,
                tenant_id=None,
                limit=50,
                offset=0,
                db=db_session,
                api_key=api_key,
            )

            assert result.total_count == 2
            assert len(result.transactions) == 2


# ============================================================================
# Integrity Endpoints Tests
# ============================================================================


class TestIntegrityRoutes:
    """Tests for Play Integrity endpoints."""

    @pytest.mark.asyncio
    async def test_get_integrity_nonce(self):
        """Get nonce returns valid response."""
        from app.api.routes import get_integrity_nonce

        with patch("app.services.play_integrity.PlayIntegrityService") as MockService:
            service = MockService.return_value
            now = datetime.now(UTC)
            service.generate_nonce.return_value = ("test-nonce", now)

            result = await get_integrity_nonce(context="purchase")

            assert result["nonce"] == "test-nonce"
            assert "expires_at" in result

    @pytest.mark.asyncio
    async def test_verify_integrity_not_configured(self):
        """Verify integrity raises 503 when not configured."""
        from fastapi import HTTPException

        from app.api.routes import verify_integrity

        with patch("app.config.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = None

            with pytest.raises(HTTPException) as exc_info:
                await verify_integrity(
                    integrity_token="test-token",
                    nonce="test-nonce",
                )

            assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_verify_integrity_with_auth_not_configured(
        self,
        user_identity: UserIdentity,
    ):
        """Verify integrity with auth handles not configured."""
        from app.api.routes import verify_integrity_with_auth

        with patch("app.config.settings") as mock_settings:
            mock_settings.PLAY_INTEGRITY_SERVICE_ACCOUNT = None

            result = await verify_integrity_with_auth(
                integrity_token="test-token",
                nonce="test-nonce",
                user=user_identity,
            )

            assert result["authenticated"] is True
            assert result["integrity_verified"] is False
            assert result["authorized"] is False
            assert "not configured" in result["reason"]


# ============================================================================
# LiteLLM Usage Route Tests
# ============================================================================


class TestGooglePubSubOIDC:
    """Tests for AV-5: Google Pub/Sub webhook must verify OIDC token."""

    def _request(self, headers: dict) -> MagicMock:
        from fastapi import Request

        request = MagicMock(spec=Request)
        request.headers = headers
        request.body = AsyncMock(return_value=b"{}")
        return request

    @pytest.mark.asyncio
    async def test_missing_authorization_header_rejected(self):
        from fastapi import HTTPException

        from app.api.routes import google_play_webhook

        request = self._request(headers={})
        with patch("app.config.settings") as mock_settings:
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = True
            with pytest.raises(HTTPException) as exc_info:
                await google_play_webhook(request=request, db=AsyncMock())
            assert exc_info.value.status_code == 401
            assert "Missing OIDC token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_oidc_token_rejected(self):
        from fastapi import HTTPException

        from app.api.routes import google_play_webhook

        request = self._request(headers={"Authorization": "Bearer bad.token.here"})

        with patch("app.config.settings") as mock_settings, patch(
            "google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("bad sig")
        ):
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = True
            mock_settings.GOOGLE_PUBSUB_AUDIENCE = "https://billing.example/webhook"
            with pytest.raises(HTTPException) as exc_info:
                await google_play_webhook(request=request, db=AsyncMock())
            assert exc_info.value.status_code == 401
            assert "Invalid OIDC token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self):
        from fastapi import HTTPException

        from app.api.routes import google_play_webhook

        request = self._request(headers={"Authorization": "Bearer good.token"})

        with patch("app.config.settings") as mock_settings, patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value={
                "iss": "https://malicious.example",
                "email_verified": True,
                "email": "x@example.com",
            },
        ):
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = True
            mock_settings.GOOGLE_PUBSUB_AUDIENCE = ""
            mock_settings.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = ""
            with pytest.raises(HTTPException) as exc_info:
                await google_play_webhook(request=request, db=AsyncMock())
            assert exc_info.value.status_code == 401
            assert "issuer" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_email_unverified_rejected(self):
        from fastapi import HTTPException

        from app.api.routes import google_play_webhook

        request = self._request(headers={"Authorization": "Bearer good.token"})

        with patch("app.config.settings") as mock_settings, patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value={
                "iss": "https://accounts.google.com",
                "email_verified": False,
                "email": "x@example.com",
            },
        ):
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = True
            mock_settings.GOOGLE_PUBSUB_AUDIENCE = ""
            mock_settings.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = ""
            with pytest.raises(HTTPException) as exc_info:
                await google_play_webhook(request=request, db=AsyncMock())
            assert exc_info.value.status_code == 401
            assert "email not verified" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_oidc_disabled_skips_validation(self):
        """When GOOGLE_PUBSUB_VERIFY_OIDC=False, the webhook accepts unsigned requests.

        This is the deployment-edge-delegation path. We log a loud warning so
        misconfiguration is visible.
        """
        from app.api.routes import google_play_webhook

        request = self._request(headers={})
        db = AsyncMock()

        with patch("app.config.settings") as mock_settings, patch(
            "app.services.provider_config.ProviderConfigService"
        ) as mock_config_cls:
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = False
            mock_config_cls.return_value.get_google_play_config = AsyncMock(
                return_value=None
            )

            result = await google_play_webhook(request=request, db=db)

            # No exception; webhook accepted (and ignored due to no provider config).
            assert result["status"] == "ignored"

    @pytest.mark.asyncio
    async def test_wrong_publisher_email_rejected(self):
        from fastapi import HTTPException

        from app.api.routes import google_play_webhook

        request = self._request(headers={"Authorization": "Bearer good.token"})

        with patch("app.config.settings") as mock_settings, patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value={
                "iss": "https://accounts.google.com",
                "email_verified": True,
                "email": "imposter@evil.iam.gserviceaccount.com",
            },
        ):
            mock_settings.GOOGLE_PUBSUB_VERIFY_OIDC = True
            mock_settings.GOOGLE_PUBSUB_AUDIENCE = ""
            mock_settings.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = (
                "expected@trusted.iam.gserviceaccount.com"
            )
            with pytest.raises(HTTPException) as exc_info:
                await google_play_webhook(request=request, db=AsyncMock())
            assert exc_info.value.status_code == 403
            assert "unauthorized publisher" in exc_info.value.detail


class TestStripeWebhookBinding:
    """Tests for the AV-3 fix: webhooks must resolve account from server-side row."""

    def _make_event(self, payment_id: str, amount: int = 500, currency: str = "USD"):
        """Build a fake parsed Stripe webhook event."""
        from app.services.payment_provider import WebhookEvent

        return WebhookEvent(
            event_id="evt_test_1",
            event_type="payment_intent.succeeded",
            payment_id=payment_id,
            status="succeeded",
            amount_minor=amount,
            currency=currency,
            metadata_account_id="ATTACKER-CONTROLLED",
            metadata_oauth_provider="oauth:google",
            metadata_external_id="attacker-external-id",
        )

    def _make_binding(
        self,
        payment_id: str,
        account_id=None,
        amount: int = 500,
        currency: str = "USD",
        status_str: str = "created",
        uses_purchased: int = 20,
    ):
        """Build a fake StripePaymentIntent row."""
        from app.db.models import StripePaymentIntent

        binding = MagicMock(spec=StripePaymentIntent)
        binding.payment_intent_id = payment_id
        binding.account_id = account_id or uuid4()
        binding.oauth_provider = "oauth:google"
        binding.external_id = "honest-user-id"
        binding.amount_minor = amount
        binding.currency = currency
        binding.uses_purchased = uses_purchased
        binding.status = status_str
        binding.credited_at = None
        return binding

    @pytest.mark.asyncio
    async def test_webhook_unknown_payment_intent_fails_closed(self):
        """Webhooks for PaymentIntents we didn't create must NOT credit any account."""
        from fastapi import HTTPException, Request

        from app.api.routes import stripe_webhook

        webhook_event = self._make_event("pi_unknown")

        # No binding row exists for this payment_id.
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b'{"signed": "payload"}')
        request.headers = {"stripe-signature": "sig"}

        with patch(
            "app.services.provider_config.ProviderConfigService"
        ) as mock_config_cls, patch(
            "app.services.stripe_provider.StripeProvider"
        ) as mock_provider_cls:
            mock_config_cls.return_value.get_stripe_config = AsyncMock(
                return_value={
                    "api_key": "sk_test",
                    "webhook_secret": "whsec_test",
                    "publishable_key": "pk_test",
                }
            )
            mock_provider = mock_provider_cls.return_value
            mock_provider.verify_webhook = AsyncMock(return_value=webhook_event)

            with pytest.raises(HTTPException) as exc_info:
                await stripe_webhook(request=request, db=db)

            assert exc_info.value.status_code == 400
            assert "Unknown PaymentIntent" in exc_info.value.detail
            # confirm_payment must NOT be called — we never reach that code path.
            mock_provider.confirm_payment.assert_not_called()

    @pytest.mark.asyncio
    async def test_webhook_amount_mismatch_fails_closed(self):
        """Webhook amount must match the bound amount or be rejected."""
        from fastapi import HTTPException, Request

        from app.api.routes import stripe_webhook

        webhook_event = self._make_event("pi_match", amount=99999)
        binding = self._make_binding("pi_match", amount=500)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: binding))

        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {"stripe-signature": "sig"}

        with patch(
            "app.services.provider_config.ProviderConfigService"
        ) as mock_config_cls, patch(
            "app.services.stripe_provider.StripeProvider"
        ) as mock_provider_cls:
            mock_config_cls.return_value.get_stripe_config = AsyncMock(
                return_value={
                    "api_key": "sk_test",
                    "webhook_secret": "whsec_test",
                    "publishable_key": "pk_test",
                }
            )
            mock_provider = mock_provider_cls.return_value
            mock_provider.verify_webhook = AsyncMock(return_value=webhook_event)

            with pytest.raises(HTTPException) as exc_info:
                await stripe_webhook(request=request, db=db)

            assert exc_info.value.status_code == 400
            assert "amount mismatch" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_webhook_happy_path_credits_bound_account(self):
        """A first-time webhook for a known PaymentIntent credits the bound account."""
        from fastapi import Request

        from app.api.routes import stripe_webhook

        webhook_event = self._make_event("pi_first")
        binding = self._make_binding("pi_first", status_str="created")

        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: binding)
        )
        db.commit = AsyncMock()

        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {"stripe-signature": "sig"}

        with patch(
            "app.services.provider_config.ProviderConfigService"
        ) as mock_config_cls, patch(
            "app.services.stripe_provider.StripeProvider"
        ) as mock_provider_cls, patch(
            "app.api.routes.BillingService"
        ) as mock_billing_cls:
            mock_config_cls.return_value.get_stripe_config = AsyncMock(
                return_value={
                    "api_key": "sk_test",
                    "webhook_secret": "whsec_test",
                    "publishable_key": "pk_test",
                }
            )
            mock_provider = mock_provider_cls.return_value
            mock_provider.verify_webhook = AsyncMock(return_value=webhook_event)
            mock_provider.confirm_payment = AsyncMock(return_value=True)

            mock_billing = mock_billing_cls.return_value
            mock_billing.add_purchased_uses = AsyncMock()

            result = await stripe_webhook(request=request, db=db)

            assert result["status"] == "success"
            mock_billing.add_purchased_uses.assert_awaited_once()
            # Account identity must come from binding, not metadata.
            kwargs = mock_billing.add_purchased_uses.call_args.kwargs
            assert kwargs["identity"].external_id == "honest-user-id"
            assert kwargs["identity"].external_id != "attacker-external-id"
            # Binding flipped to "credited"
            assert binding.status == "credited"
            assert binding.credited_at is not None

    @pytest.mark.asyncio
    async def test_webhook_already_credited_returns_idempotent(self):
        """Webhook redelivery for an already-credited PaymentIntent does not re-credit."""
        from fastapi import Request

        from app.api.routes import stripe_webhook

        webhook_event = self._make_event("pi_credited")
        binding = self._make_binding("pi_credited", status_str="credited")

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: binding))

        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"{}")
        request.headers = {"stripe-signature": "sig"}

        with patch(
            "app.services.provider_config.ProviderConfigService"
        ) as mock_config_cls, patch(
            "app.services.stripe_provider.StripeProvider"
        ) as mock_provider_cls:
            mock_config_cls.return_value.get_stripe_config = AsyncMock(
                return_value={
                    "api_key": "sk_test",
                    "webhook_secret": "whsec_test",
                    "publishable_key": "pk_test",
                }
            )
            mock_provider = mock_provider_cls.return_value
            mock_provider.verify_webhook = AsyncMock(return_value=webhook_event)

            result = await stripe_webhook(request=request, db=db)

            assert result["status"] == "already_credited"
            mock_provider.confirm_payment.assert_not_called()


class TestLiteLLMUsageRoutes:
    """Tests for LiteLLM usage endpoints."""
