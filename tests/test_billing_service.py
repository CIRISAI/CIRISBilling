"""
Tests for BillingService - Core business logic tests.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    AccountNotFoundError,
    InsufficientCreditsError,
    IdempotencyConflictError,
)
from app.models.api import ChargeMetadata, TransactionType
from app.models.domain import AccountIdentity, ChargeIntent, CreditIntent
from app.services.billing import BillingService


class TestCreditCheck:
    """Tests for credit check operations."""

    async def test_credit_check_account_not_found(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test credit check for non-existent account."""
        service = BillingService(db_session)
        result = await service.check_credit(test_account_identity)

        assert result.has_credit is False
        assert result.credits_remaining is None
        assert result.reason == "Account not found"

    async def test_credit_check_sufficient_balance(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test credit check with sufficient balance."""
        service = BillingService(db_session)

        # Create account with balance
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=1000,
        )
        await db_session.commit()

        # Check credit
        result = await service.check_credit(test_account_identity)

        assert result.has_credit is True
        assert result.credits_remaining == 1000
        assert result.reason is None

    async def test_credit_check_insufficient_balance(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test credit check with zero balance."""
        service = BillingService(db_session)

        # Create account with zero balance
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=0,
        )
        await db_session.commit()

        # Check credit
        result = await service.check_credit(test_account_identity)

        assert result.has_credit is False
        assert result.credits_remaining == 0
        assert result.reason == "Insufficient credits"


class TestChargeCreation:
    """Tests for charge creation operations."""

    async def test_create_charge_success(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test successful charge creation."""
        service = BillingService(db_session)

        # Create account with balance
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=1000,
        )
        await db_session.commit()

        # Create charge
        intent = ChargeIntent(
            account_identity=test_account_identity,
            amount_minor=100,
            currency="USD",
            description="Test charge",
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )

        charge_data = await service.create_charge(intent)

        assert charge_data.amount_minor == 100
        assert charge_data.balance_before == 1000
        assert charge_data.balance_after == 900
        assert charge_data.currency == "USD"

    async def test_create_charge_insufficient_balance(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test charge creation with insufficient balance."""
        service = BillingService(db_session)

        # Create account with low balance
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=50,
        )
        await db_session.commit()

        # Attempt charge that exceeds balance
        intent = ChargeIntent(
            account_identity=test_account_identity,
            amount_minor=100,
            currency="USD",
            description="Test charge",
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )

        with pytest.raises(InsufficientCreditsError) as exc_info:
            await service.create_charge(intent)

        assert exc_info.value.balance == 50
        assert exc_info.value.required == 100

    async def test_create_charge_account_not_found(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test charge creation for non-existent account."""
        service = BillingService(db_session)

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

    async def test_create_charge_idempotency(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test charge idempotency - duplicate key should raise error."""
        service = BillingService(db_session)

        # Create account
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=1000,
        )
        await db_session.commit()

        # Create first charge
        intent1 = ChargeIntent(
            account_identity=test_account_identity,
            amount_minor=100,
            currency="USD",
            description="Test charge",
            metadata=ChargeMetadata(),
            idempotency_key="test-key-123",
        )
        await service.create_charge(intent1)

        # Attempt duplicate charge with same idempotency key
        intent2 = ChargeIntent(
            account_identity=test_account_identity,
            amount_minor=100,
            currency="USD",
            description="Test charge",
            metadata=ChargeMetadata(),
            idempotency_key="test-key-123",
        )

        with pytest.raises(IdempotencyConflictError):
            await service.create_charge(intent2)


class TestCreditAddition:
    """Tests for credit addition operations."""

    async def test_add_credits_success(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test successful credit addition."""
        service = BillingService(db_session)

        # Create account
        await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=0,
        )
        await db_session.commit()

        # Add credits
        intent = CreditIntent(
            account_identity=test_account_identity,
            amount_minor=500,
            currency="USD",
            description="Test credit",
            transaction_type=TransactionType.PURCHASE,
            external_transaction_id="stripe-123",
            idempotency_key=None,
        )

        credit_data = await service.add_credits(intent)

        assert credit_data.amount_minor == 500
        assert credit_data.balance_before == 0
        assert credit_data.balance_after == 500
        assert credit_data.transaction_type == TransactionType.PURCHASE

    async def test_add_credits_account_not_found(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test credit addition for non-existent account."""
        service = BillingService(db_session)

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
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test account creation when it doesn't exist."""
        service = BillingService(db_session)

        account_data = await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=100,
            currency="USD",
            plan_name="free",
        )

        assert account_data.oauth_provider == test_account_identity.oauth_provider
        assert account_data.external_id == test_account_identity.external_id
        assert account_data.balance_minor == 100
        assert account_data.currency == "USD"
        assert account_data.plan_name == "free"

    async def test_get_or_create_account_returns_existing(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test that existing account is returned."""
        service = BillingService(db_session)

        # Create account
        account1 = await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=100,
        )
        await db_session.commit()

        # Try to create again
        account2 = await service.get_or_create_account(
            test_account_identity,
            initial_balance_minor=200,  # Different balance
        )

        # Should return existing account with original balance
        assert account1.account_id == account2.account_id
        assert account2.balance_minor == 100  # Original balance, not 200

    async def test_get_account_success(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test getting existing account."""
        service = BillingService(db_session)

        # Create account
        await service.get_or_create_account(test_account_identity)
        await db_session.commit()

        # Get account
        account_data = await service.get_account(test_account_identity)

        assert account_data.oauth_provider == test_account_identity.oauth_provider
        assert account_data.external_id == test_account_identity.external_id

    async def test_get_account_not_found(
        self, db_session: AsyncSession, test_account_identity: AccountIdentity
    ):
        """Test getting non-existent account."""
        service = BillingService(db_session)

        with pytest.raises(AccountNotFoundError):
            await service.get_account(test_account_identity)
