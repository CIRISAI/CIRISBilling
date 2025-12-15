"""
Tests for Product Inventory Service.

Tests product-specific credit management including web search credits.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Account, ProductInventory, ProductUsageLog
from app.exceptions import InsufficientCreditsError, ResourceNotFoundError
from app.models.domain import AccountIdentity
from app.services.product_inventory import (
    PRODUCT_CONFIGS,
    ProductBalance,
    ProductChargeResult,
    ProductConfig,
    ProductInventoryService,
)


class TestProductConfig:
    """Tests for ProductConfig dataclass."""

    def test_product_config_attributes(self):
        """ProductConfig has correct attributes."""
        config = ProductConfig(
            free_initial=10,
            free_daily=5,
            price_minor=100,
        )
        assert config.free_initial == 10
        assert config.free_daily == 5
        assert config.price_minor == 100

    def test_web_search_config_exists(self):
        """web_search product is configured."""
        assert "web_search" in PRODUCT_CONFIGS
        config = PRODUCT_CONFIGS["web_search"]
        assert config.free_initial >= 0
        assert config.free_daily >= 0
        assert config.price_minor >= 0


class TestProductBalance:
    """Tests for ProductBalance dataclass."""

    def test_product_balance_attributes(self):
        """ProductBalance has correct attributes."""
        balance = ProductBalance(
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            main_pool_credits=100,
            total_available=115,
            price_minor=100,
            total_uses=50,
        )
        assert balance.product_type == "web_search"
        assert balance.free_remaining == 5
        assert balance.paid_credits == 10
        assert balance.main_pool_credits == 100
        assert balance.total_available == 115
        assert balance.price_minor == 100
        assert balance.total_uses == 50


class TestProductChargeResult:
    """Tests for ProductChargeResult dataclass."""

    def test_charge_result_attributes(self):
        """ProductChargeResult has correct attributes."""
        result = ProductChargeResult(
            success=True,
            used_free=True,
            used_paid=False,
            cost_minor=0,
            free_remaining=4,
            paid_credits=10,
            total_uses=51,
            usage_log_id=uuid4(),
        )
        assert result.success is True
        assert result.used_free is True
        assert result.used_paid is False
        assert result.cost_minor == 0


class TestProductInventoryService:
    """Tests for ProductInventoryService."""

    @pytest.fixture
    def db_session(self):
        """Create mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        return session

    @pytest.fixture
    def test_identity(self):
        """Create test account identity."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id=None,
        )

    @pytest.fixture
    def mock_account(self):
        """Create mock account."""
        account = MagicMock(spec=Account)
        account.id = uuid4()
        account.oauth_provider = "oauth:google"
        account.external_id = "user@example.com"
        account.wa_id = None
        account.tenant_id = None
        account.status = "active"
        account.paid_credits = 0  # Main credit pool
        account.balance_minor = 0
        return account

    @pytest.fixture
    def mock_inventory(self):
        """Create mock product inventory."""
        inventory = MagicMock(spec=ProductInventory)
        inventory.id = uuid4()
        inventory.account_id = uuid4()
        inventory.product_type = "web_search"
        inventory.free_remaining = 5
        inventory.paid_credits = 10
        inventory.total_uses = 50
        inventory.last_daily_refresh = datetime.now(UTC)
        return inventory

    @pytest.mark.asyncio
    async def test_find_account_by_identity_found(self, db_session, test_identity, mock_account):
        """_find_account_by_identity returns account when found."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_account
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        account = await service._find_account_by_identity(test_identity)

        assert account == mock_account

    @pytest.mark.asyncio
    async def test_find_account_by_identity_not_found(self, db_session, test_identity):
        """_find_account_by_identity returns None when not found."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        account = await service._find_account_by_identity(test_identity)

        assert account is None

    @pytest.mark.asyncio
    async def test_get_or_create_account_existing(self, db_session, test_identity, mock_account):
        """_get_or_create_account returns existing account."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_account
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        account = await service._get_or_create_account(test_identity)

        assert account == mock_account

    @pytest.mark.asyncio
    async def test_get_or_create_account_creates_new(self, db_session, test_identity):
        """_get_or_create_account creates new account when not found."""
        # First query returns None (not found)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)

        with patch("app.services.product_inventory.settings") as mock_settings:
            mock_settings.free_uses_per_account = 10
            account = await service._get_or_create_account(test_identity)

        assert account is not None
        db_session.add.assert_called_once()
        db_session.flush.assert_called_once()
        db_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_account_handles_race_condition(
        self, db_session, test_identity, mock_account
    ):
        """_get_or_create_account handles race condition during creation."""
        result = MagicMock()
        # First find returns None, then after rollback returns the account
        result.scalar_one_or_none.side_effect = [None, mock_account]
        db_session.execute.return_value = result

        # Simulate integrity error from race condition
        db_session.flush.side_effect = IntegrityError("", "", None)

        service = ProductInventoryService(db_session)

        with patch("app.services.product_inventory.settings") as mock_settings:
            mock_settings.free_uses_per_account = 10
            account = await service._get_or_create_account(test_identity)

        assert account == mock_account
        db_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_account_race_condition_no_account_raises(
        self, db_session, test_identity
    ):
        """_get_or_create_account raises when race condition but still no account."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result

        db_session.flush.side_effect = IntegrityError("", "", None)

        service = ProductInventoryService(db_session)

        with patch("app.services.product_inventory.settings") as mock_settings:
            mock_settings.free_uses_per_account = 10
            with pytest.raises(ResourceNotFoundError):
                await service._get_or_create_account(test_identity)

    @pytest.mark.asyncio
    async def test_get_or_create_inventory_existing(self, db_session, mock_account, mock_inventory):
        """get_or_create_inventory returns existing inventory."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_inventory
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        inventory = await service.get_or_create_inventory(mock_account.id, "web_search")

        assert inventory == mock_inventory

    @pytest.mark.asyncio
    async def test_get_or_create_inventory_creates_new(self, db_session, mock_account):
        """get_or_create_inventory creates new inventory when not found."""
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        inventory = await service.get_or_create_inventory(mock_account.id, "web_search")

        assert inventory is not None
        db_session.add.assert_called_once()
        db_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_inventory_unknown_product_raises(self, db_session, mock_account):
        """get_or_create_inventory raises for unknown product type."""
        service = ProductInventoryService(db_session)

        with pytest.raises(ValueError, match="Unknown product type"):
            await service.get_or_create_inventory(mock_account.id, "invalid_product")

    def test_should_refresh_daily_no_last_refresh(self, db_session, mock_inventory):
        """_should_refresh_daily returns True when no last refresh."""
        mock_inventory.last_daily_refresh = None

        service = ProductInventoryService(db_session)
        assert service._should_refresh_daily(mock_inventory) is True

    def test_should_refresh_daily_same_day(self, db_session, mock_inventory):
        """_should_refresh_daily returns False for same day."""
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        service = ProductInventoryService(db_session)
        assert service._should_refresh_daily(mock_inventory) is False

    def test_should_refresh_daily_new_day(self, db_session, mock_inventory):
        """_should_refresh_daily returns True for new day."""
        mock_inventory.last_daily_refresh = datetime.now(UTC) - timedelta(days=1)

        service = ProductInventoryService(db_session)
        assert service._should_refresh_daily(mock_inventory) is True

    def test_refresh_daily_credits_when_needed(self, db_session, mock_inventory):
        """_refresh_daily_credits adds credits when needed."""
        mock_inventory.last_daily_refresh = datetime.now(UTC) - timedelta(days=1)
        mock_inventory.free_remaining = 0

        service = ProductInventoryService(db_session)
        refreshed = service._refresh_daily_credits(mock_inventory)

        assert refreshed is True
        assert mock_inventory.free_remaining > 0

    def test_refresh_daily_credits_not_needed(self, db_session, mock_inventory):
        """_refresh_daily_credits does nothing when not needed."""
        mock_inventory.last_daily_refresh = datetime.now(UTC)
        original_free = mock_inventory.free_remaining

        service = ProductInventoryService(db_session)
        refreshed = service._refresh_daily_credits(mock_inventory)

        assert refreshed is False
        assert mock_inventory.free_remaining == original_free

    @pytest.mark.asyncio
    async def test_get_balance_returns_correct_balance(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """get_balance returns correct product balance."""
        # Mock account lookup
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        # Mock inventory lookup
        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory

        db_session.execute.side_effect = [account_result, inventory_result]

        service = ProductInventoryService(db_session)
        balance = await service.get_balance(test_identity, "web_search")

        assert balance.product_type == "web_search"
        assert balance.free_remaining == mock_inventory.free_remaining
        assert balance.paid_credits == mock_inventory.paid_credits
        # Total includes free + paid + main_pool_credits
        # main_pool_credits = mock_account.paid_credits // price_minor (0 // 1 = 0)
        assert balance.main_pool_credits == 0
        assert (
            balance.total_available
            == mock_inventory.free_remaining
            + mock_inventory.paid_credits
            + balance.main_pool_credits
        )

    @pytest.mark.asyncio
    async def test_check_credit_true_when_available(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """check_credit returns True when credits available."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 5
        mock_inventory.paid_credits = 10

        db_session.execute.side_effect = [account_result, inventory_result]

        service = ProductInventoryService(db_session)
        has_credit = await service.check_credit(test_identity, "web_search")

        assert has_credit is True

    @pytest.mark.asyncio
    async def test_check_credit_false_when_no_credits(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """check_credit returns False when no credits available."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 0
        mock_inventory.paid_credits = 0
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        db_session.execute.side_effect = [account_result, inventory_result]

        service = ProductInventoryService(db_session)
        has_credit = await service.check_credit(test_identity, "web_search")

        assert has_credit is False

    @pytest.mark.asyncio
    async def test_charge_uses_free_credits_first(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge uses free credits before paid credits."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 5
        mock_inventory.paid_credits = 10
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        # No existing idempotency log
        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)
        result = await service.charge(test_identity, "web_search")

        assert result.success is True
        assert result.used_free is True
        assert result.used_paid is False
        assert result.cost_minor == 0
        assert mock_inventory.free_remaining == 4

    @pytest.mark.asyncio
    async def test_charge_uses_paid_credits_when_no_free(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge uses paid credits when no free credits available."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 0
        mock_inventory.paid_credits = 10
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)
        result = await service.charge(test_identity, "web_search")

        assert result.success is True
        assert result.used_free is False
        assert result.used_paid is True
        assert result.cost_minor > 0  # Should be price_minor from config
        assert mock_inventory.paid_credits == 9

    @pytest.mark.asyncio
    async def test_charge_raises_insufficient_credits(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge raises InsufficientCreditsError when no credits available."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 0
        mock_inventory.paid_credits = 0
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)

        with pytest.raises(InsufficientCreditsError):
            await service.charge(test_identity, "web_search")

    @pytest.mark.asyncio
    async def test_charge_idempotency_returns_existing(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge returns existing result for duplicate idempotency key."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory

        # Existing log for idempotency key
        existing_log = MagicMock(spec=ProductUsageLog)
        existing_log.id = uuid4()
        existing_log.used_free = True
        existing_log.used_paid = False
        existing_log.cost_minor = 0

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = existing_log

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)
        result = await service.charge(test_identity, "web_search", idempotency_key="test-key-123")

        assert result.success is True
        assert result.usage_log_id == existing_log.id
        # Verify no new charge was made
        db_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_increments_total_uses(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge increments total_uses counter."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 5
        mock_inventory.total_uses = 50
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)
        result = await service.charge(test_identity, "web_search")

        assert mock_inventory.total_uses == 51
        assert result.total_uses == 51

    @pytest.mark.asyncio
    async def test_charge_uses_main_pool_when_tool_credits_exhausted(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """charge uses main account pool when free and tool paid credits are exhausted."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account
        # Account has main pool credits (568 cents = 568 tool uses at 1 cent each)
        mock_account.paid_credits = 568
        mock_account.balance_minor = 568

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        # No free or tool-specific paid credits
        mock_inventory.free_remaining = 0
        mock_inventory.paid_credits = 0
        mock_inventory.last_daily_refresh = datetime.now(UTC)
        mock_inventory.total_uses = 50

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        service = ProductInventoryService(db_session)
        result = await service.charge(test_identity, "web_search")

        # Should succeed by using main pool
        assert result.success is True
        assert result.used_paid is True
        # Main pool should be decremented by price_minor (1 cent)
        assert mock_account.paid_credits == 567
        assert mock_account.balance_minor == 567
        # Cost should be the price_minor
        assert result.cost_minor == 1

    @pytest.mark.asyncio
    async def test_get_balance_includes_main_pool_credits(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """get_balance includes main pool credits in total_available."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account
        # Account has 100 main pool credits
        mock_account.paid_credits = 100

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.free_remaining = 5
        mock_inventory.paid_credits = 10
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        db_session.execute.side_effect = [account_result, inventory_result]

        service = ProductInventoryService(db_session)
        balance = await service.get_balance(test_identity, "web_search")

        # main_pool_credits = 100 // 1 = 100 tool uses
        assert balance.main_pool_credits == 100
        # total = 5 free + 10 paid + 100 main pool = 115
        assert balance.total_available == 115

    @pytest.mark.asyncio
    async def test_add_credits_increases_paid_credits(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """add_credits increases paid_credits."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.paid_credits = 10

        db_session.execute.side_effect = [account_result, inventory_result]

        service = ProductInventoryService(db_session)
        balance = await service.add_credits(test_identity, "web_search", credits=25)

        assert mock_inventory.paid_credits == 35
        assert balance.paid_credits == 35

    @pytest.mark.asyncio
    async def test_get_all_balances_returns_all_products(
        self, db_session, test_identity, mock_account, mock_inventory
    ):
        """get_all_balances returns balance for all product types."""
        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        # Return account, then inventory for each product type
        db_session.execute.side_effect = [account_result] + [
            inventory_result for _ in PRODUCT_CONFIGS
        ]

        service = ProductInventoryService(db_session)
        balances = await service.get_all_balances(test_identity)

        assert len(balances) == len(PRODUCT_CONFIGS)
        for balance in balances:
            assert balance.product_type in PRODUCT_CONFIGS


class TestProductInventoryEdgeCases:
    """Edge case tests for ProductInventoryService."""

    @pytest.fixture
    def db_session(self):
        """Create mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        return session

    @pytest.fixture
    def test_identity_with_wa_id(self):
        """Create test identity with WhatsApp ID."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id="1234567890",
            tenant_id=None,
        )

    @pytest.fixture
    def test_identity_with_tenant(self):
        """Create test identity with tenant ID."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id="tenant-123",
        )

    @pytest.mark.asyncio
    async def test_find_account_with_wa_id(self, db_session, test_identity_with_wa_id):
        """_find_account_by_identity handles wa_id correctly."""
        mock_account = MagicMock(spec=Account)
        mock_account.id = uuid4()
        mock_account.wa_id = "1234567890"

        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_account
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        account = await service._find_account_by_identity(test_identity_with_wa_id)

        assert account == mock_account
        db_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_account_with_tenant_id(self, db_session, test_identity_with_tenant):
        """_find_account_by_identity handles tenant_id correctly."""
        mock_account = MagicMock(spec=Account)
        mock_account.id = uuid4()
        mock_account.tenant_id = "tenant-123"

        result = MagicMock()
        result.scalar_one_or_none.return_value = mock_account
        db_session.execute.return_value = result

        service = ProductInventoryService(db_session)
        account = await service._find_account_by_identity(test_identity_with_tenant)

        assert account == mock_account

    def test_refresh_daily_credits_caps_at_max(self, db_session):
        """_refresh_daily_credits caps credits at initial + daily."""
        mock_inventory = MagicMock(spec=ProductInventory)
        mock_inventory.product_type = "web_search"
        mock_inventory.last_daily_refresh = datetime.now(UTC) - timedelta(days=1)
        mock_inventory.free_remaining = 100  # Already high

        config = PRODUCT_CONFIGS["web_search"]
        max_expected = config.free_initial + config.free_daily

        service = ProductInventoryService(db_session)
        service._refresh_daily_credits(mock_inventory)

        # Should be capped at initial + daily, not 100 + daily
        assert mock_inventory.free_remaining <= max_expected

    @pytest.mark.asyncio
    async def test_charge_creates_usage_log(self, db_session):
        """charge creates ProductUsageLog entry."""
        test_identity = AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id=None,
        )

        mock_account = MagicMock(spec=Account)
        mock_account.id = uuid4()

        mock_inventory = MagicMock(spec=ProductInventory)
        mock_inventory.free_remaining = 5
        mock_inventory.paid_credits = 10
        mock_inventory.total_uses = 0
        mock_inventory.last_daily_refresh = datetime.now(UTC)

        account_result = MagicMock()
        account_result.scalar_one_or_none.return_value = mock_account

        inventory_result = MagicMock()
        inventory_result.scalar_one_or_none.return_value = mock_inventory

        idempotency_result = MagicMock()
        idempotency_result.scalar_one_or_none.return_value = None

        db_session.execute.side_effect = [
            account_result,
            inventory_result,
            idempotency_result,
        ]

        added_objects = []
        db_session.add = MagicMock(side_effect=lambda x: added_objects.append(x))

        service = ProductInventoryService(db_session)
        await service.charge(
            test_identity, "web_search", idempotency_key="key-1", request_id="req-1"
        )

        # Verify usage log was added
        assert len(added_objects) == 1
        usage_log = added_objects[0]
        assert usage_log.account_id == mock_account.id
        assert usage_log.product_type == "web_search"
        assert usage_log.idempotency_key == "key-1"
        assert usage_log.request_id == "req-1"
