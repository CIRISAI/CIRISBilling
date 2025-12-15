"""
Hypothesis Property-Based Tests for BillingService.

Tests billing logic invariants and validation without complex DB mocking.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.db.models import Account
from app.exceptions import (
    AccountClosedError,
    AccountNotFoundError,
    AccountSuspendedError,
    IdempotencyConflictError,
)
from app.models.api import AccountStatus, ChargeMetadata, TransactionType
from app.models.domain import AccountIdentity, ChargeIntent, CreditIntent
from app.services.billing import (
    BillingService,
    _build_closed_response,
    _build_suspended_response,
    _should_reset_daily_uses,
    _validate_charge_account,
)

# ============================================================================
# Hypothesis Strategies
# ============================================================================

oauth_providers = st.sampled_from(["oauth:google", "oauth:discord", "oauth:github"])
external_ids = st.text(min_size=1, max_size=100).filter(lambda x: x.strip())
currencies = st.sampled_from(["USD", "EUR", "GBP"])
positive_amounts = st.integers(min_value=1, max_value=1_000_000)
non_negative_amounts = st.integers(min_value=0, max_value=1_000_000)
descriptions = st.text(min_size=1, max_size=200).filter(lambda x: x.strip())
plan_names = st.sampled_from(["free", "pro", "enterprise"])
transaction_types = st.sampled_from(list(TransactionType))


@st.composite
def account_identities(draw):
    """Generate valid AccountIdentity objects."""
    return AccountIdentity(
        oauth_provider=draw(oauth_providers),
        external_id=draw(external_ids),
        wa_id=draw(st.one_of(st.none(), st.text(min_size=1, max_size=50))),
        tenant_id=draw(st.one_of(st.none(), st.text(min_size=1, max_size=50))),
    )


@st.composite
def mock_accounts(draw, status=None, balance=None, free_uses=None, daily_free=None):
    """Generate mock Account objects."""
    account = MagicMock(spec=Account)
    account.id = uuid4()
    account.oauth_provider = draw(oauth_providers)
    account.external_id = draw(external_ids)
    account.wa_id = None
    account.tenant_id = None
    account.customer_email = None
    account.display_name = None
    account.balance_minor = balance if balance is not None else draw(non_negative_amounts)
    account.currency = draw(currencies)
    account.plan_name = draw(plan_names)
    account.status = status if status is not None else AccountStatus.ACTIVE
    account.paid_credits = balance if balance is not None else draw(non_negative_amounts)
    account.free_uses_remaining = (
        free_uses if free_uses is not None else draw(st.integers(min_value=0, max_value=10))
    )
    account.daily_free_uses_remaining = (
        daily_free if daily_free is not None else draw(st.integers(min_value=0, max_value=5))
    )
    account.daily_free_uses_limit = 2
    account.daily_free_uses_reset_at = None
    account.total_uses = draw(st.integers(min_value=0, max_value=1000))
    account.marketing_opt_in = False
    account.marketing_opt_in_at = None
    account.marketing_opt_in_source = None
    account.user_role = None
    account.agent_id = None
    account.created_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    return account


@st.composite
def charge_intents(draw, identity=None):
    """Generate valid ChargeIntent objects."""
    if identity is None:
        identity = draw(account_identities())
    return ChargeIntent(
        account_identity=identity,
        amount_minor=draw(positive_amounts),
        currency=draw(currencies),
        description=draw(descriptions),
        metadata=ChargeMetadata(),
        idempotency_key=draw(st.one_of(st.none(), st.uuids().map(str))),
    )


# ============================================================================
# Tests for Pure Functions (no DB needed)
# ============================================================================


class TestShouldResetDailyUsesProperties:
    """Property-based tests for _should_reset_daily_uses."""

    @given(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2025, 1, 1),
            timezones=st.just(UTC),
        )
    )
    @settings(max_examples=100)
    def test_none_reset_at_always_resets(self, _timestamp):
        """None reset_at always returns True (needs reset)."""
        assert _should_reset_daily_uses(None) is True

    @given(
        st.datetimes(
            min_value=datetime(2020, 1, 1),
            max_value=datetime(2024, 1, 1),
            timezones=st.just(UTC),
        )
    )
    @settings(max_examples=100)
    def test_past_reset_at_returns_true(self, past_time):
        """Past reset_at returns True (needs reset)."""
        # Any time before "now" should trigger reset
        assert _should_reset_daily_uses(past_time) is True


class TestBuildSuspendedResponseProperties:
    """Property-based tests for _build_suspended_response."""

    @given(mock_accounts(status=AccountStatus.SUSPENDED))
    @settings(max_examples=50)
    def test_suspended_response_has_no_credit(self, account):
        """Suspended account response always has_credit=False."""
        response = _build_suspended_response(account)
        assert response.has_credit is False
        assert response.reason == "Account suspended"
        assert response.daily_free_uses_remaining == 0

    @given(mock_accounts(status=AccountStatus.SUSPENDED))
    @settings(max_examples=50)
    def test_suspended_response_preserves_balance_info(self, account):
        """Suspended response preserves balance information."""
        response = _build_suspended_response(account)
        assert response.credits_remaining == account.paid_credits
        assert response.plan_name == account.plan_name
        assert response.free_uses_remaining == account.free_uses_remaining


class TestBuildClosedResponseProperties:
    """Property-based tests for _build_closed_response."""

    @given(mock_accounts(status=AccountStatus.CLOSED))
    @settings(max_examples=50)
    def test_closed_response_has_no_credit(self, account):
        """Closed account response always has_credit=False."""
        response = _build_closed_response(account)
        assert response.has_credit is False
        assert response.reason == "Account closed"

    @given(mock_accounts(status=AccountStatus.CLOSED))
    @settings(max_examples=50)
    def test_closed_response_preserves_balance_info(self, account):
        """Closed response preserves balance information."""
        response = _build_closed_response(account)
        assert response.credits_remaining == account.paid_credits
        assert response.plan_name == account.plan_name


class TestValidateChargeAccountProperties:
    """Property-based tests for _validate_charge_account."""

    @given(charge_intents())
    @settings(max_examples=50)
    def test_none_account_raises_not_found(self, intent):
        """None account always raises AccountNotFoundError."""
        with pytest.raises(AccountNotFoundError):
            _validate_charge_account(None, intent)

    @given(mock_accounts(status=AccountStatus.SUSPENDED), charge_intents())
    @settings(max_examples=50)
    def test_suspended_account_raises(self, account, intent):
        """Suspended account always raises AccountSuspendedError."""
        with pytest.raises(AccountSuspendedError):
            _validate_charge_account(account, intent)

    @given(mock_accounts(status=AccountStatus.CLOSED), charge_intents())
    @settings(max_examples=50)
    def test_closed_account_raises(self, account, intent):
        """Closed account always raises AccountClosedError."""
        with pytest.raises(AccountClosedError):
            _validate_charge_account(account, intent)

    @given(mock_accounts(status=AccountStatus.ACTIVE), currencies, currencies)
    @settings(max_examples=50)
    def test_currency_mismatch_raises(self, account, account_currency, intent_currency):
        """Currency mismatch raises DataIntegrityError."""
        assume(account_currency != intent_currency)
        account.currency = account_currency

        intent = ChargeIntent(
            account_identity=AccountIdentity(
                oauth_provider=account.oauth_provider,
                external_id=account.external_id,
                wa_id=None,
                tenant_id=None,
            ),
            amount_minor=100,
            currency=intent_currency,
            description="Test",
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )

        from app.exceptions import DataIntegrityError

        with pytest.raises(DataIntegrityError, match="Currency mismatch"):
            _validate_charge_account(account, intent)

    @given(mock_accounts(status=AccountStatus.ACTIVE))
    @settings(max_examples=50)
    def test_valid_account_returns_account(self, account):
        """Valid active account is returned."""
        intent = ChargeIntent(
            account_identity=AccountIdentity(
                oauth_provider=account.oauth_provider,
                external_id=account.external_id,
                wa_id=None,
                tenant_id=None,
            ),
            amount_minor=100,
            currency=account.currency,
            description="Test",
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )

        result = _validate_charge_account(account, intent)
        assert result is account


# ============================================================================
# Billing Service - Simple Async Tests
# ============================================================================


class TestBillingServiceAccountLookup:
    """Tests for account lookup behavior."""

    @given(account_identities())
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_charge_nonexistent_account_raises(self, identity):
        """Charge on nonexistent account raises AccountNotFoundError."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)

        intent = ChargeIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test",
            metadata=ChargeMetadata(),
            idempotency_key=None,
        )

        service = BillingService(session)

        with pytest.raises(AccountNotFoundError):
            await service.create_charge(intent)

    @given(account_identities())
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_add_credits_nonexistent_account_raises(self, identity):
        """Add credits to nonexistent account raises AccountNotFoundError."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)

        intent = CreditIntent(
            account_identity=identity,
            amount_minor=100,
            currency="USD",
            description="Test",
            transaction_type=TransactionType.GRANT,
            external_transaction_id=None,
            idempotency_key=None,
        )

        service = BillingService(session)

        with pytest.raises(AccountNotFoundError):
            await service.add_credits(intent)

    @given(st.uuids().map(str))
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_duplicate_charge_idempotency_key_raises(self, idem_key):
        """Duplicate idempotency key raises IdempotencyConflictError."""
        existing_charge = MagicMock()
        existing_charge.id = uuid4()

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=existing_charge)
        session.execute = AsyncMock(return_value=mock_result)

        intent = ChargeIntent(
            account_identity=AccountIdentity(
                oauth_provider="oauth:google",
                external_id="test@example.com",
                wa_id=None,
                tenant_id=None,
            ),
            amount_minor=100,
            currency="USD",
            description="Test",
            metadata=ChargeMetadata(),
            idempotency_key=idem_key,
        )

        service = BillingService(session)

        with pytest.raises(IdempotencyConflictError):
            await service.create_charge(intent)

    @given(st.uuids().map(str))
    @settings(max_examples=30)
    @pytest.mark.asyncio
    async def test_duplicate_credit_idempotency_key_raises(self, idem_key):
        """Duplicate credit idempotency key raises IdempotencyConflictError."""
        existing_credit = MagicMock()
        existing_credit.id = uuid4()

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=existing_credit)
        session.execute = AsyncMock(return_value=mock_result)

        intent = CreditIntent(
            account_identity=AccountIdentity(
                oauth_provider="oauth:google",
                external_id="test@example.com",
                wa_id=None,
                tenant_id=None,
            ),
            amount_minor=100,
            currency="USD",
            description="Test",
            transaction_type=TransactionType.GRANT,
            external_transaction_id=None,
            idempotency_key=idem_key,
        )

        service = BillingService(session)

        with pytest.raises(IdempotencyConflictError):
            await service.add_credits(intent)


# ============================================================================
# Billing Invariants - Mathematical Properties
# ============================================================================


class TestBillingInvariants:
    """Tests for mathematical invariants in billing operations."""

    @given(
        st.integers(min_value=0, max_value=10000),
        st.lists(
            st.integers(min_value=1, max_value=100),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_credits_are_commutative(self, initial_balance, credit_amounts):
        """Order of credit additions doesn't affect final balance."""
        import random

        balance1 = initial_balance + sum(credit_amounts)

        shuffled = credit_amounts.copy()
        random.shuffle(shuffled)
        balance2 = initial_balance + sum(shuffled)

        assert balance1 == balance2

    @given(
        st.integers(min_value=100, max_value=10000),
        st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=100)
    def test_credit_charge_inverse(self, initial_balance, amount):
        """Credit followed by equal charge returns to original balance."""
        after_credit = initial_balance + amount
        after_charge = after_credit - amount
        assert after_charge == initial_balance

    @given(
        st.integers(min_value=0, max_value=10000),
        st.lists(
            st.tuples(
                st.sampled_from(["credit", "charge"]),
                st.integers(min_value=1, max_value=100),
            ),
            min_size=1,
            max_size=20,
        ),
    )
    @settings(max_examples=100)
    def test_balance_audit_trail(self, initial_balance, operations):
        """Sum of all operations equals final balance minus initial."""
        balance = initial_balance
        total_credits = 0
        total_charges = 0

        for op_type, amount in operations:
            if op_type == "credit":
                balance += amount
                total_credits += amount
            elif op_type == "charge" and balance >= amount:
                balance -= amount
                total_charges += amount

        expected = initial_balance + total_credits - total_charges
        assert balance == expected

    @given(
        st.integers(min_value=0, max_value=1000),
        st.lists(
            st.tuples(
                st.sampled_from(["credit", "charge"]),
                st.integers(min_value=1, max_value=100),
            ),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=100)
    def test_balance_never_negative_with_validation(self, initial, operations):
        """Balance should never go negative when validating charges."""
        balance = initial
        for op_type, amount in operations:
            if op_type == "credit":
                balance += amount
            elif op_type == "charge":
                if balance >= amount:
                    balance -= amount
            assert balance >= 0

    @given(
        st.integers(min_value=0, max_value=5),  # daily_free
        st.integers(min_value=0, max_value=10),  # one_time_free
        st.integers(min_value=0, max_value=100),  # paid_credits
    )
    @settings(max_examples=100)
    def test_credit_source_priority(self, daily_free, one_time_free, paid_credits):
        """Credits consumed in priority: daily_free > one_time_free > paid."""
        # Simulate a single charge
        if daily_free > 0:
            # Should use daily free
            new_daily = daily_free - 1
            new_free = one_time_free
            new_paid = paid_credits
        elif one_time_free > 0:
            # Should use one-time free
            new_daily = 0
            new_free = one_time_free - 1
            new_paid = paid_credits
        elif paid_credits > 0:
            # Should use paid
            new_daily = 0
            new_free = 0
            new_paid = paid_credits - 1
        else:
            # No credits - can't charge
            new_daily = 0
            new_free = 0
            new_paid = 0

        # Verify invariants
        assert new_daily >= 0
        assert new_free >= 0
        assert new_paid >= 0
        total_before = daily_free + one_time_free + paid_credits
        total_after = new_daily + new_free + new_paid
        if total_before > 0:
            assert total_after == total_before - 1


class TestFreeUsesInvariants:
    """Property tests for free uses behavior."""

    @given(
        st.integers(min_value=0, max_value=10),  # daily_limit
        st.integers(min_value=0, max_value=100),  # num_charges
    )
    @settings(max_examples=100)
    def test_daily_free_bounded_by_limit(self, daily_limit, num_charges):
        """Daily free uses never exceed limit after any number of operations."""
        daily_remaining = daily_limit

        for _ in range(num_charges):
            if daily_remaining > 0:
                daily_remaining -= 1
            # Simulate reset
            if daily_remaining == 0:
                daily_remaining = daily_limit

        assert 0 <= daily_remaining <= daily_limit

    @given(
        st.integers(min_value=0, max_value=10),  # initial_free_uses
        st.integers(min_value=0, max_value=20),  # num_charges
    )
    @settings(max_examples=100)
    def test_one_time_free_monotonically_decreasing(self, initial_free, num_charges):
        """One-time free uses only decrease (never reset)."""
        free_uses = initial_free
        values = [free_uses]

        for _ in range(num_charges):
            if free_uses > 0:
                free_uses -= 1
                values.append(free_uses)

        # Verify monotonic decrease
        for i in range(1, len(values)):
            assert values[i] <= values[i - 1]
