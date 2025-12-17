"""
Tests for BillingService.

Unit tests for billing service operations.
"""

from datetime import UTC, datetime, timedelta
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


def create_mock_account(
    identity: AccountIdentity,
    balance_minor: int = 0,
    paid_credits: int = 0,
    free_uses_remaining: int = 3,
    status: str = "active",
    daily_free_uses_remaining: int = 0,
    daily_free_uses_limit: int = 2,
    daily_free_uses_reset_at: datetime | None = None,
    customer_email: str | None = None,
    display_name: str | None = None,
) -> MagicMock:
    """Create a mock Account with given parameters."""
    account = MagicMock(spec=Account)
    account.id = uuid4()
    account.oauth_provider = identity.oauth_provider
    account.external_id = identity.external_id
    account.wa_id = identity.wa_id
    account.tenant_id = identity.tenant_id
    account.customer_email = customer_email
    account.display_name = display_name
    account.balance_minor = balance_minor
    account.currency = "USD"
    account.plan_name = "free"
    account.status = status
    account.free_uses_remaining = free_uses_remaining
    account.total_uses = 0
    account.paid_credits = paid_credits
    account.daily_free_uses_remaining = daily_free_uses_remaining
    account.daily_free_uses_limit = daily_free_uses_limit
    account.daily_free_uses_reset_at = daily_free_uses_reset_at
    account.marketing_opt_in = False
    account.marketing_opt_in_at = None
    account.marketing_opt_in_source = None
    account.user_role = None
    account.agent_id = None
    account.created_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    return account


class TestCreditCheck:
    """Tests for credit check operations."""

    async def test_credit_check_new_account_gets_free_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit check for new account - should auto-create with free uses."""
        # Create a mock account that represents the newly created account
        mock_account = create_mock_account(
            test_account_identity,
            free_uses_remaining=3,
            paid_credits=0,
            daily_free_uses_remaining=2,
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=datetime.now(UTC) + timedelta(hours=12),
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            # Account doesn't exist, return mock account after creation
            mock_find.return_value = mock_account

            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                # Mock settings at the config module level
                with patch("app.config.settings") as mock_settings:
                    mock_settings.price_per_purchase_minor = 199
                    mock_settings.paid_uses_per_purchase = 20

                    result = await service.check_credit(test_account_identity)

        # Account with free uses has credit
        assert result.has_credit is True
        assert result.free_uses_remaining == 3

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
        """Test credit check with no free uses, daily uses, or paid credits."""
        # Set reset time in the future so daily uses don't get reset
        future_reset = datetime.now(UTC) + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            free_uses_remaining=0,
            paid_credits=0,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=future_reset,
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
            test_account_identity,
            free_uses_remaining=3,
            paid_credits=0,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=datetime.now(UTC) + timedelta(hours=12),
        )
        mock_account.id = account_id

        # Create mock charge that will be "created"
        mock_charge = MagicMock(spec=Charge)
        mock_charge.id = uuid4()
        mock_charge.account_id = account_id
        mock_charge.amount_minor = 1
        mock_charge.currency = "USD"
        mock_charge.balance_before = 0
        mock_charge.balance_after = 0
        mock_charge.description = "Test charge"
        mock_charge.metadata_message_id = None
        mock_charge.metadata_agent_id = None
        mock_charge.metadata_channel_id = None
        mock_charge.metadata_request_id = None
        mock_charge.created_at = datetime.now(UTC)

        service = BillingService(db_session)

        # Track what gets added and set up session.get
        added_charge = None

        def capture_add(obj):
            nonlocal added_charge
            if hasattr(obj, "account_id"):
                obj.id = mock_charge.id
                added_charge = obj

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: mock_charge if id == mock_charge.id else mock_account
        )

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None  # No existing charge

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_lock:
                mock_lock.return_value = mock_account

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
            test_account_identity,
            free_uses_remaining=0,
            paid_credits=100,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=datetime.now(UTC) + timedelta(hours=12),
        )
        mock_account.id = account_id

        # Create mock charge
        mock_charge = MagicMock(spec=Charge)
        mock_charge.id = uuid4()
        mock_charge.account_id = account_id
        mock_charge.amount_minor = 10
        mock_charge.currency = "USD"
        mock_charge.balance_before = 100
        mock_charge.balance_after = 90
        mock_charge.description = "Test charge"
        mock_charge.metadata_message_id = None
        mock_charge.metadata_agent_id = None
        mock_charge.metadata_channel_id = None
        mock_charge.metadata_request_id = None
        mock_charge.created_at = datetime.now(UTC)

        service = BillingService(db_session)

        def capture_add(obj):
            if hasattr(obj, "account_id"):
                obj.id = mock_charge.id

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: mock_charge if id == mock_charge.id else mock_account
        )

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_lock:
                mock_lock.return_value = mock_account

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

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
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
            test_account_identity,
            free_uses_remaining=0,
            paid_credits=50,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=datetime.now(UTC) + timedelta(hours=12),
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_lock:
                mock_lock.return_value = mock_account

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

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
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

        # Create mock credit that will be "created"
        from app.db.models import Credit

        mock_credit = MagicMock(spec=Credit)
        mock_credit.id = uuid4()
        mock_credit.account_id = account_id
        mock_credit.amount_minor = 500
        mock_credit.currency = "USD"
        mock_credit.balance_before = 0
        mock_credit.balance_after = 500
        mock_credit.transaction_type = TransactionType.PURCHASE
        mock_credit.description = "Test credit"
        mock_credit.external_transaction_id = "stripe-123"
        mock_credit.created_at = datetime.now(UTC)

        service = BillingService(db_session)

        def capture_add(obj):
            if hasattr(obj, "account_id") and hasattr(obj, "transaction_type"):
                obj.id = mock_credit.id

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: mock_credit if id == mock_credit.id else mock_account
        )

        with patch.object(
            service, "_find_credit_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_lock:
                mock_lock.return_value = mock_account

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
            service, "_find_credit_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
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

    async def test_add_credits_with_is_test_true(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit addition with is_test=True for test purchases."""
        account_id = uuid4()
        mock_account = create_mock_account(test_account_identity, paid_credits=0)
        mock_account.id = account_id

        service = BillingService(db_session)

        # Track the Credit object that gets added
        added_credit = None

        def capture_add(obj):
            nonlocal added_credit
            from app.db.models import Credit

            if isinstance(obj, Credit):
                obj.id = uuid4()  # Simulate ID generation
                added_credit = obj

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: added_credit
            if added_credit and added_credit.id == id
            else mock_account
        )

        with patch.object(
            service, "_find_credit_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = CreditIntent(
                    account_identity=test_account_identity,
                    amount_minor=500,
                    currency="USD",
                    description="Test credit (test purchase)",
                    transaction_type=TransactionType.PURCHASE,
                    external_transaction_id="GPA.test-123",
                    idempotency_key=None,
                    is_test=True,
                )

                await service.add_credits(intent)

        # Credits should be added
        assert mock_account.paid_credits == 500
        # Verify Credit was created with is_test=True
        assert added_credit is not None
        assert added_credit.is_test is True

    async def test_add_credits_with_is_test_false(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit addition with is_test=False for real purchases."""
        account_id = uuid4()
        mock_account = create_mock_account(test_account_identity, paid_credits=0)
        mock_account.id = account_id

        service = BillingService(db_session)

        # Track the Credit object that gets added
        added_credit = None

        def capture_add(obj):
            nonlocal added_credit
            from app.db.models import Credit

            if isinstance(obj, Credit):
                obj.id = uuid4()  # Simulate ID generation
                added_credit = obj

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: added_credit
            if added_credit and added_credit.id == id
            else mock_account
        )

        with patch.object(
            service, "_find_credit_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = CreditIntent(
                    account_identity=test_account_identity,
                    amount_minor=500,
                    currency="USD",
                    description="Real credit purchase",
                    transaction_type=TransactionType.PURCHASE,
                    external_transaction_id="GPA.real-123",
                    idempotency_key=None,
                    is_test=False,
                )

                await service.add_credits(intent)

        # Credits should be added
        assert mock_account.paid_credits == 500
        # Verify Credit was created with is_test=False
        assert added_credit is not None
        assert added_credit.is_test is False

    async def test_add_credits_default_is_test_false(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test credit addition defaults is_test to False."""
        account_id = uuid4()
        mock_account = create_mock_account(test_account_identity, paid_credits=0)
        mock_account.id = account_id

        service = BillingService(db_session)

        # Track the Credit object that gets added
        added_credit = None

        def capture_add(obj):
            nonlocal added_credit
            from app.db.models import Credit

            if isinstance(obj, Credit):
                obj.id = uuid4()  # Simulate ID generation
                added_credit = obj

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(
            side_effect=lambda model, id: added_credit
            if added_credit and added_credit.id == id
            else mock_account
        )

        with patch.object(
            service, "_find_credit_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                # Create intent without explicitly setting is_test
                intent = CreditIntent(
                    account_identity=test_account_identity,
                    amount_minor=500,
                    currency="USD",
                    description="Credit purchase",
                    transaction_type=TransactionType.PURCHASE,
                    external_transaction_id="stripe-123",
                    idempotency_key=None,
                )

                await service.add_credits(intent)

        # Verify is_test defaults to False
        assert added_credit is not None
        assert added_credit.is_test is False


class TestAccountManagement:
    """Tests for account management operations."""

    async def test_get_or_create_account_creates_new(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test account creation when it doesn't exist."""
        # Create a mock account that will be returned after creation
        mock_account = create_mock_account(
            test_account_identity,
            balance_minor=100,
            daily_free_uses_remaining=2,
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=datetime.now(UTC) + timedelta(hours=12),
        )

        service = BillingService(db_session)

        # Capture the account that's added
        added_account = None

        def capture_add(obj):
            nonlocal added_account
            if hasattr(obj, "oauth_provider"):
                obj.id = mock_account.id
                added_account = obj

        db_session.add = MagicMock(side_effect=capture_add)
        db_session.get = AsyncMock(return_value=mock_account)

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


class TestDisplayName:
    """Tests for display_name functionality."""

    async def test_update_account_metadata_sets_display_name(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that update_account_metadata sets display_name."""
        mock_account = create_mock_account(test_account_identity, display_name=None)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            await service.update_account_metadata(
                identity=test_account_identity,
                display_name="John Doe",
            )

        assert mock_account.display_name == "John Doe"
        db_session.commit.assert_called()

    async def test_update_account_metadata_updates_display_name(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that update_account_metadata updates existing display_name."""
        mock_account = create_mock_account(test_account_identity, display_name="Old Name")

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            await service.update_account_metadata(
                identity=test_account_identity,
                display_name="New Name",
            )

        assert mock_account.display_name == "New Name"
        db_session.commit.assert_called()

    async def test_update_account_metadata_no_change_when_same(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that update_account_metadata doesn't commit when name unchanged."""
        mock_account = create_mock_account(test_account_identity, display_name="Same Name")

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            await service.update_account_metadata(
                identity=test_account_identity,
                display_name="Same Name",
            )

        # Should not commit when value is unchanged
        db_session.commit.assert_not_called()

    async def test_update_account_metadata_with_email_and_name(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that update_account_metadata can update both email and name."""
        mock_account = create_mock_account(
            test_account_identity, customer_email=None, display_name=None
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            await service.update_account_metadata(
                identity=test_account_identity,
                customer_email="user@example.com",
                display_name="Jane Doe",
            )

        assert mock_account.customer_email == "user@example.com"
        assert mock_account.display_name == "Jane Doe"
        db_session.commit.assert_called()

    async def test_get_or_create_account_with_display_name(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test account creation includes display_name."""
        service = BillingService(db_session)

        # Track the account that gets added
        added_account = None

        def capture_add(account):
            nonlocal added_account
            added_account = account

        db_session.add = MagicMock(side_effect=capture_add)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = None  # Account doesn't exist

            db_session.flush = AsyncMock()
            db_session.commit = AsyncMock()
            # Mock session.get to return the added account after flush
            db_session.get = AsyncMock(side_effect=lambda model, id: added_account)

            await service.get_or_create_account(
                test_account_identity,
                initial_balance_minor=0,
                currency="USD",
                plan_name="free",
                customer_email="user@example.com",
                display_name="Test User",
            )

        # Check that add was called with an account
        assert added_account is not None
        # The account should have the display_name set
        assert added_account.display_name == "Test User"
        assert added_account.customer_email == "user@example.com"


class TestDuplicateAccountHandling:
    """Tests for handling duplicate accounts gracefully (BILLING-001)."""

    async def test_find_account_handles_multiple_results(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that _find_account_by_identity handles duplicate accounts gracefully."""
        from sqlalchemy.exc import MultipleResultsFound

        # Create two mock accounts (duplicates)
        older_account = create_mock_account(test_account_identity)
        older_account.created_at = datetime(2025, 1, 1, tzinfo=UTC)

        newer_account = create_mock_account(test_account_identity)
        newer_account.created_at = datetime(2025, 12, 17, tzinfo=UTC)

        service = BillingService(db_session)

        # First call raises MultipleResultsFound, second returns oldest
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # First query - simulate MultipleResultsFound
                result.scalar_one_or_none = MagicMock(side_effect=MultipleResultsFound())
            else:
                # Second query (ordered) - return the oldest account
                result.scalar_one_or_none = MagicMock(return_value=older_account)
            return result

        db_session.execute = mock_execute

        # Should not raise, should return the older account
        result = await service._find_account_by_identity(test_account_identity)

        assert result is not None
        assert result.created_at == older_account.created_at

    async def test_lock_account_handles_multiple_results(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test that _lock_account_for_update handles duplicate accounts gracefully."""
        from sqlalchemy.exc import MultipleResultsFound

        older_account = create_mock_account(test_account_identity)
        older_account.created_at = datetime(2025, 1, 1, tzinfo=UTC)

        service = BillingService(db_session)

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(side_effect=MultipleResultsFound())
            else:
                result.scalar_one_or_none = MagicMock(return_value=older_account)
            return result

        db_session.execute = mock_execute

        result = await service._lock_account_for_update(test_account_identity)

        assert result is not None
        assert result.created_at == older_account.created_at

    async def test_get_or_create_handles_integrity_error_race(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Test get_or_create_account handles IntegrityError from race condition."""
        from sqlalchemy.exc import IntegrityError

        existing_account = create_mock_account(test_account_identity, paid_credits=10)

        service = BillingService(db_session)

        find_call_count = 0

        async def mock_find(identity):
            nonlocal find_call_count
            find_call_count += 1
            if find_call_count == 1:
                return None  # First call: no account exists
            return existing_account  # After IntegrityError: account exists

        # Flush raises IntegrityError (constraint violation from race)
        db_session.flush = AsyncMock(
            side_effect=IntegrityError("duplicate key", None, None)
        )
        db_session.rollback = AsyncMock()

        with patch.object(service, "_find_account_by_identity", side_effect=mock_find):
            result = await service.get_or_create_account(
                test_account_identity,
                initial_balance_minor=0,
            )

        # Should return the existing account after handling the race
        assert result.paid_credits == 10
        db_session.rollback.assert_called_once()
