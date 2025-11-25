"""
Tests for BillingService - Integration tests requiring database.

Run with: pytest -m integration
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import Account, Charge
from app.exceptions import (
    AccountNotFoundError,
    IdempotencyConflictError,
    InsufficientCreditsError,
)
from app.models.api import AccountStatus, ChargeMetadata, TransactionType
from app.models.domain import AccountIdentity, ChargeIntent, CreditIntent
from app.services.billing import BillingService

# Mark all tests in this module as integration tests (require database)
pytestmark = pytest.mark.integration


def create_mock_account(
    identity: AccountIdentity,
    balance_minor: int = 0,
    paid_credits: int = 0,
    free_uses_remaining: int = 3,
    status: str = "active",
) -> MagicMock:
    """Create a mock Account with given parameters."""
    account = MagicMock(spec=Account)
    account.id = uuid4()
    account.oauth_provider = identity.oauth_provider
    account.external_id = identity.external_id
    account.wa_id = identity.wa_id
    account.tenant_id = identity.tenant_id
    account.customer_email = None
    account.balance_minor = balance_minor
    account.currency = "USD"
    account.plan_name = "free"
    account.status = status
    account.free_uses_remaining = free_uses_remaining
    account.total_uses = 0
    account.paid_credits = paid_credits
    account.marketing_opt_in = False
    account.marketing_opt_in_at = None
    account.marketing_opt_in_source = None
    account.created_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    return account


class TestCreditCheck:
    """Tests for credit check operations."""

    async def test_credit_check_new_account_gets_free_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check for new account - should auto-create with free uses."""
        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = None

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                db_session.flush = AsyncMock()
                db_session.commit = AsyncMock()

                result = await service.check_credit(test_account_identity)

        # New accounts get free uses
        assert result.has_credit is True
        assert result.free_uses_remaining == 3  # Default free uses
        db_session.add.assert_called()

    async def test_credit_check_with_free_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check with free uses remaining."""
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=2, paid_credits=0
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                result = await service.check_credit(test_account_identity)

        assert result.has_credit is True
        assert result.free_uses_remaining == 2
        assert result.plan_name == "free"

    async def test_credit_check_with_paid_credits(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check with paid credits (no free uses)."""
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=0, paid_credits=100
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                result = await service.check_credit(test_account_identity)

        assert result.has_credit is True
        assert result.credits_remaining == 100

    async def test_credit_check_no_credits(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check with no free uses or paid credits."""
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=0, paid_credits=0
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                result = await service.check_credit(test_account_identity)

        assert result.has_credit is False
        assert result.purchase_required is True

    async def test_credit_check_suspended_account(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check for suspended account."""
        mock_account = create_mock_account(
            test_account_identity, paid_credits=100, status=AccountStatus.SUSPENDED
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                result = await service.check_credit(test_account_identity)

        assert result.has_credit is False
        assert result.reason is not None
        assert "suspended" in result.reason.lower()


class TestChargeCreation:
    """Tests for charge creation operations."""

    async def test_create_charge_uses_free_tier(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test charge uses free tier when available."""
        account_id = uuid4()
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=3, paid_credits=0
        )
        mock_account.id = account_id

        service = BillingService(db_session)

        with patch.object(service, "_check_idempotency", new_callable=AsyncMock) as mock_idemp:
            mock_idemp.return_value = None  # No existing charge

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,  # 1 credit/use
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # Free tier used - account.free_uses_remaining should be decremented
        assert mock_account.free_uses_remaining == 2
        assert mock_account.total_uses == 1
        db_session.add.assert_called()
        db_session.commit.assert_called()

    async def test_create_charge_uses_paid_credits(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test charge uses paid credits when no free tier."""
        account_id = uuid4()
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=0, paid_credits=100
        )
        mock_account.id = account_id

        service = BillingService(db_session)

        with patch.object(service, "_check_idempotency", new_callable=AsyncMock) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=10,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # Paid credits should be decremented
        assert mock_account.paid_credits == 90
        assert mock_account.total_uses == 1
        db_session.commit.assert_called()

    async def test_create_charge_account_not_found(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test charge creation for non-existent account."""
        service = BillingService(db_session)

        with patch.object(service, "_check_idempotency", new_callable=AsyncMock) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = None  # Account not found

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=100,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                with pytest.raises(AccountNotFoundError):
                    await service.create_charge(intent)

    async def test_create_charge_insufficient_credits(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test charge creation with insufficient paid credits and no free uses."""
        mock_account = create_mock_account(
            test_account_identity, free_uses_remaining=0, paid_credits=50
        )

        service = BillingService(db_session)

        with patch.object(service, "_check_idempotency", new_callable=AsyncMock) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=100,  # More than paid_credits
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                with pytest.raises(InsufficientCreditsError) as exc_info:
                    await service.create_charge(intent)

        assert exc_info.value.balance == 50
        assert exc_info.value.required == 100

    async def test_create_charge_idempotency_conflict(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test charge idempotency - duplicate key should raise error."""
        existing_charge = MagicMock(spec=Charge)
        existing_charge.id = uuid4()

        service = BillingService(db_session)

        with patch.object(service, "_check_idempotency", new_callable=AsyncMock) as mock_idemp:
            mock_idemp.return_value = existing_charge  # Existing charge found

            intent = ChargeIntent(
                account_identity=test_account_identity,
                amount_minor=100,
                currency="USD",
                description="Test charge",
                metadata=ChargeMetadata(),
                idempotency_key="test-key-123",
            )

            with pytest.raises(IdempotencyConflictError):
                await service.create_charge(intent)


class TestCreditAddition:
    """Tests for credit addition operations."""

    async def test_add_credits_success(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test successful credit addition."""
        account_id = uuid4()
        mock_account = create_mock_account(test_account_identity, paid_credits=0)
        mock_account.id = account_id

        service = BillingService(db_session)

        with patch.object(
            service, "_check_credit_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = CreditIntent(
                    account_identity=test_account_identity,
                    amount_minor=500,
                    currency="USD",
                    description="Test credit",
                    transaction_type=TransactionType.PURCHASE,
                    external_transaction_id="stripe-123",
                    idempotency_key=None,
                )

                await service.add_credits(intent)

        # Credits should be added
        assert mock_account.paid_credits == 500
        db_session.add.assert_called()
        db_session.commit.assert_called()

    async def test_add_credits_account_not_found(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit addition for non-existent account."""
        service = BillingService(db_session)

        with patch.object(
            service, "_check_credit_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_get_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = None

                intent = CreditIntent(
                    account_identity=test_account_identity,
                    amount_minor=500,
                    currency="USD",
                    description="Test credit",
                    transaction_type=TransactionType.PURCHASE,
                    external_transaction_id=None,
                    idempotency_key=None,
                )

                with pytest.raises(AccountNotFoundError):
                    await service.add_credits(intent)


class TestAccountManagement:
    """Tests for account management operations."""

    async def test_get_or_create_account_creates_new(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test account creation when it doesn't exist."""
        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = None  # Account doesn't exist

            db_session.flush = AsyncMock()
            db_session.commit = AsyncMock()

            result = await service.get_or_create_account(
                test_account_identity,
                initial_balance_minor=100,
                currency="USD",
                plan_name="free",
            )

        assert result.oauth_provider == test_account_identity.oauth_provider
        assert result.external_id == test_account_identity.external_id
        db_session.add.assert_called()

    async def test_get_or_create_account_returns_existing(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that existing account is returned."""
        existing_account = create_mock_account(test_account_identity, balance_minor=100)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = existing_account

            result = await service.get_or_create_account(
                test_account_identity,
                initial_balance_minor=200,  # Should be ignored
            )

        # Should return existing account with original balance
        assert result.balance_minor == 100
        db_session.add.assert_not_called()

    async def test_get_account_success(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test getting existing account."""
        existing_account = create_mock_account(test_account_identity)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = existing_account

            result = await service.get_account(test_account_identity)

        assert result.oauth_provider == test_account_identity.oauth_provider
        assert result.external_id == test_account_identity.external_id

    async def test_get_account_not_found(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test getting non-existent account."""
        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = None

            with pytest.raises(AccountNotFoundError):
                await service.get_account(test_account_identity)
