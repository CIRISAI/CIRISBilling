"""
Tests for Daily Free Uses Feature.

Tests the daily reset logic, priority (daily > one-time > paid),
and edge cases like timezone handling.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.db.models import Account, Charge
from app.exceptions import InsufficientCreditsError
from app.models.api import ChargeMetadata
from app.models.domain import AccountIdentity, ChargeIntent
from app.services.billing import (
    BillingService,
    _get_next_reset_time,
    _should_reset_daily_uses,
    _utc_now,
)


def create_mock_charge(account_id: UUID) -> MagicMock:
    """Create a mock Charge for verification."""
    charge = MagicMock(spec=Charge)
    charge.id = uuid4()
    charge.account_id = account_id
    charge.amount_minor = 1
    charge.currency = "USD"
    charge.balance_before = 0
    charge.balance_after = 0
    charge.description = "Test charge"
    charge.metadata_message_id = None
    charge.metadata_agent_id = None
    charge.metadata_channel_id = None
    charge.metadata_request_id = None
    charge.created_at = datetime.now(UTC)
    return charge


pytestmark = pytest.mark.integration


def create_mock_account(
    identity: AccountIdentity,
    paid_credits: int = 0,
    free_uses_remaining: int = 0,
    daily_free_uses_remaining: int = 2,
    daily_free_uses_limit: int = 2,
    daily_free_uses_reset_at: datetime | None = None,
    status: str = "active",
) -> MagicMock:
    """Create a mock Account with daily free uses support."""
    account = MagicMock(spec=Account)
    account.id = uuid4()
    account.oauth_provider = identity.oauth_provider
    account.external_id = identity.external_id
    account.wa_id = identity.wa_id
    account.tenant_id = identity.tenant_id
    account.customer_email = None
    account.balance_minor = 0
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
    account.created_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    return account


class TestDailyFreeUsesReset:
    """Tests for daily reset logic."""

    def test_should_reset_when_no_reset_at(self) -> None:
        """Should reset when daily_free_uses_reset_at is None."""
        assert _should_reset_daily_uses(None) is True

    def test_should_reset_when_past_reset_time(self) -> None:
        """Should reset when current time is past reset_at."""
        past_time = _utc_now() - timedelta(hours=1)
        assert _should_reset_daily_uses(past_time) is True

    def test_should_not_reset_when_before_reset_time(self) -> None:
        """Should NOT reset when current time is before reset_at."""
        future_time = _utc_now() + timedelta(hours=1)
        assert _should_reset_daily_uses(future_time) is False

    def test_get_next_reset_time_is_tomorrow_midnight_utc(self) -> None:
        """Next reset time should be midnight UTC tomorrow."""
        reset_time = _get_next_reset_time()
        now = _utc_now()

        # Should be tomorrow
        assert reset_time.date() == (now.date() + timedelta(days=1))
        # Should be midnight
        assert reset_time.hour == 0
        assert reset_time.minute == 0
        assert reset_time.second == 0
        # Should have UTC timezone
        assert reset_time.tzinfo == UTC


class TestCreditCheckDailyFreeUses:
    """Tests for credit check with daily free uses."""

    async def test_credit_check_with_daily_free_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Credit check should show daily free uses as available."""
        # Reset time is in the future (still valid)
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=2,
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=0,
            paid_credits=0,
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account
            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                result = await service.check_credit(test_account_identity)

        assert result.has_credit is True
        assert result.daily_free_uses_remaining == 2
        assert result.daily_free_uses_limit == 2
        assert result.purchase_required is False

    async def test_credit_check_resets_daily_uses_when_expired(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Credit check should reset daily uses when past reset time."""
        # Reset time is in the past (expired)
        past_reset = _utc_now() - timedelta(hours=1)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,  # Was used up
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=past_reset,
            free_uses_remaining=0,
            paid_credits=0,
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account
            with patch.object(service, "_log_credit_check", new_callable=AsyncMock):
                db_session.flush = AsyncMock()
                db_session.commit = AsyncMock()

                result = await service.check_credit(test_account_identity)

        # Should show full daily free uses after reset
        assert result.has_credit is True
        assert result.daily_free_uses_remaining == 2
        # Account should have been updated
        assert mock_account.daily_free_uses_remaining == 2
        assert mock_account.daily_free_uses_reset_at is not None

    async def test_credit_check_no_credit_when_all_exhausted(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Credit check should return no credit when daily, free, and paid all exhausted."""
        # Reset time is in the future (can't reset yet)
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=0,
            paid_credits=0,
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


class TestChargeDailyFreeUses:
    """Tests for charge creation with daily free uses priority."""

    async def test_charge_uses_daily_free_first(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Charge should use daily free uses before one-time free or paid."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=2,
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=5,  # Still has one-time free
            paid_credits=100,  # Still has paid
        )
        mock_charge = create_mock_charge(mock_account.id)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account
                # Return charge first, then account for verification
                db_session.get = AsyncMock(side_effect=[mock_charge, mock_account])

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # Daily free should be decremented, others unchanged
        assert mock_account.daily_free_uses_remaining == 1
        assert mock_account.free_uses_remaining == 5  # Unchanged
        assert mock_account.paid_credits == 100  # Unchanged
        assert mock_account.total_uses == 1

    async def test_charge_uses_onetime_free_when_daily_exhausted(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Charge should use one-time free when daily free is exhausted."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,  # Exhausted
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=5,  # Has one-time free
            paid_credits=100,  # Has paid
        )
        mock_charge = create_mock_charge(mock_account.id)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account
                db_session.get = AsyncMock(side_effect=[mock_charge, mock_account])

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # One-time free should be decremented
        assert mock_account.daily_free_uses_remaining == 0  # Still 0
        assert mock_account.free_uses_remaining == 4  # Decremented
        assert mock_account.paid_credits == 100  # Unchanged

    async def test_charge_uses_paid_when_free_exhausted(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Charge should use paid credits when both daily and one-time free are exhausted."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,  # Exhausted
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=0,  # Exhausted
            paid_credits=100,  # Has paid
        )
        mock_charge = create_mock_charge(mock_account.id)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account
                db_session.get = AsyncMock(side_effect=[mock_charge, mock_account])

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # Paid should be decremented
        assert mock_account.daily_free_uses_remaining == 0
        assert mock_account.free_uses_remaining == 0
        assert mock_account.paid_credits == 99  # Decremented

    async def test_charge_resets_daily_uses_before_check(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Charge should reset daily uses if expired, then use them."""
        past_reset = _utc_now() - timedelta(hours=1)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,  # Was exhausted
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=past_reset,  # Expired
            free_uses_remaining=0,
            paid_credits=100,
        )
        mock_charge = create_mock_charge(mock_account.id)

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account
                db_session.get = AsyncMock(side_effect=[mock_charge, mock_account])

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                await service.create_charge(intent)

        # Should have reset to 2, then decremented to 1
        assert mock_account.daily_free_uses_remaining == 1
        assert mock_account.paid_credits == 100  # Unchanged - used daily free

    async def test_charge_fails_when_all_exhausted(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """Charge should fail when daily, free, and paid all exhausted."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,
            daily_free_uses_reset_at=future_reset,  # Can't reset yet
            free_uses_remaining=0,
            paid_credits=0,
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_charge_by_idempotency", new_callable=AsyncMock
        ) as mock_idemp:
            mock_idemp.return_value = None

            with patch.object(
                service, "_lock_account_for_update", new_callable=AsyncMock
            ) as mock_get:
                mock_get.return_value = mock_account

                intent = ChargeIntent(
                    account_identity=test_account_identity,
                    amount_minor=1,
                    currency="USD",
                    description="Test charge",
                    metadata=ChargeMetadata(),
                    idempotency_key=None,
                )

                with pytest.raises(InsufficientCreditsError):
                    await service.create_charge(intent)


class TestAccountDataDailyFreeUses:
    """Tests for AccountData with daily free uses."""

    async def test_account_to_domain_includes_daily_free_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """AccountData should include daily free uses fields."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=1,
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=future_reset,
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            result = await service.get_account(test_account_identity)

        assert result.daily_free_uses_remaining == 1
        assert result.daily_free_uses_limit == 2
        assert result.daily_free_uses_reset_at == future_reset

    async def test_account_to_domain_resets_expired_daily_uses(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """AccountData should show reset daily uses if expired."""
        past_reset = _utc_now() - timedelta(hours=1)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=0,  # Was exhausted
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=past_reset,  # Expired
        )

        service = BillingService(db_session)

        with patch.object(
            service, "_find_account_by_identity", new_callable=AsyncMock
        ) as mock_find:
            mock_find.return_value = mock_account

            result = await service.get_account(test_account_identity)

        # Should show full daily uses after reset
        assert result.daily_free_uses_remaining == 2
        # Reset time should be tomorrow
        assert result.daily_free_uses_reset_at is not None
        assert result.daily_free_uses_reset_at > _utc_now()


class TestMultipleDailyUsesInDay:
    """Tests for using multiple daily free uses within a day."""

    async def test_use_all_daily_free_then_paid(
        self, db_session: AsyncMock, test_account_identity: AccountIdentity
    ) -> None:
        """User should be able to use all daily free uses, then paid."""
        future_reset = _utc_now() + timedelta(hours=12)
        mock_account = create_mock_account(
            test_account_identity,
            daily_free_uses_remaining=2,
            daily_free_uses_limit=2,
            daily_free_uses_reset_at=future_reset,
            free_uses_remaining=0,
            paid_credits=10,
        )

        service = BillingService(db_session)

        # Simulate 3 charges in sequence
        for i in range(3):
            mock_charge = create_mock_charge(mock_account.id)
            with patch.object(
                service, "_find_charge_by_idempotency", new_callable=AsyncMock
            ) as mock_idemp:
                mock_idemp.return_value = None

                with patch.object(
                    service, "_lock_account_for_update", new_callable=AsyncMock
                ) as mock_get:
                    mock_get.return_value = mock_account
                    db_session.get = AsyncMock(side_effect=[mock_charge, mock_account])

                    intent = ChargeIntent(
                        account_identity=test_account_identity,
                        amount_minor=1,
                        currency="USD",
                        description=f"Test charge {i+1}",
                        metadata=ChargeMetadata(),
                        idempotency_key=f"key-{i}",
                    )

                    await service.create_charge(intent)

        # First 2 should use daily free, 3rd should use paid
        assert mock_account.daily_free_uses_remaining == 0
        assert mock_account.paid_credits == 9
        assert mock_account.total_uses == 3
