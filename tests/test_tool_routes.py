"""
Tests for Tool API Routes.

Tests endpoints for tool credit management (web search, image gen, etc.)
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.exceptions import InsufficientCreditsError, ResourceNotFoundError
from app.models.domain import AccountIdentity
from app.services.product_inventory import ProductBalance, ProductChargeResult


class TestToolBalanceEndpoints:
    """Tests for tool balance endpoints."""

    @pytest.fixture
    def mock_identity(self):
        """Create mock account identity."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id=None,
        )

    @pytest.fixture
    def mock_balance(self):
        """Create mock product balance."""
        return ProductBalance(
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            main_pool_credits=0,
            total_available=15,
            price_minor=100,
            total_uses=50,
        )

    @pytest.mark.asyncio
    async def test_get_tool_balance_success(self, mock_identity, mock_balance):
        """get_tool_balance returns balance for product type."""
        from app.api.tool_routes import get_tool_balance

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(return_value=mock_balance)

            result = await get_tool_balance(
                product_type="web_search",
                identity=mock_identity,
                db=db,
            )

        assert result.product_type == "web_search"
        assert result.free_remaining == 5
        assert result.paid_credits == 10
        assert result.total_available == 15
        assert result.price_minor == 100
        assert result.total_uses == 50

    @pytest.mark.asyncio
    async def test_get_tool_balance_invalid_product(self, mock_identity):
        """get_tool_balance raises 400 for unknown product type."""
        from fastapi import HTTPException

        from app.api.tool_routes import get_tool_balance

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(
                side_effect=ValueError("Unknown product type: invalid")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_tool_balance(
                    product_type="invalid",
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_tool_balance_not_found(self, mock_identity):
        """get_tool_balance raises 404 when account not found."""
        from fastapi import HTTPException

        from app.api.tool_routes import get_tool_balance

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(
                side_effect=ResourceNotFoundError("Account not found")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_tool_balance(
                    product_type="web_search",
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_all_tool_balances_success(self, mock_identity, mock_balance):
        """get_all_tool_balances returns all product balances."""
        from app.api.tool_routes import get_all_tool_balances

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_all_balances = AsyncMock(return_value=[mock_balance])

            result = await get_all_tool_balances(
                identity=mock_identity,
                db=db,
            )

        assert len(result.balances) == 1
        assert result.balances[0].product_type == "web_search"

    @pytest.mark.asyncio
    async def test_get_all_tool_balances_not_found(self, mock_identity):
        """get_all_tool_balances raises 404 when account not found."""
        from fastapi import HTTPException

        from app.api.tool_routes import get_all_tool_balances

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_all_balances = AsyncMock(
                side_effect=ResourceNotFoundError("Account not found")
            )

            with pytest.raises(HTTPException) as exc_info:
                await get_all_tool_balances(
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 404


class TestToolCheckEndpoint:
    """Tests for tool check endpoint."""

    @pytest.fixture
    def mock_identity(self):
        """Create mock account identity."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id=None,
        )

    @pytest.mark.asyncio
    async def test_check_tool_credit_has_credit(self, mock_identity):
        """check_tool_credit returns has_credit=True when credits available."""
        from app.api.tool_routes import check_tool_credit

        mock_balance = ProductBalance(
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            main_pool_credits=0,
            total_available=15,
            price_minor=100,
            total_uses=50,
        )

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(return_value=mock_balance)

            result = await check_tool_credit(
                product_type="web_search",
                identity=mock_identity,
                db=db,
            )

        assert result.has_credit is True
        assert result.product_type == "web_search"
        assert result.total_available == 15

    @pytest.mark.asyncio
    async def test_check_tool_credit_no_credit(self, mock_identity):
        """check_tool_credit returns has_credit=False when no credits."""
        from app.api.tool_routes import check_tool_credit

        mock_balance = ProductBalance(
            product_type="web_search",
            free_remaining=0,
            paid_credits=0,
            main_pool_credits=0,
            total_available=0,
            price_minor=100,
            total_uses=50,
        )

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(return_value=mock_balance)

            result = await check_tool_credit(
                product_type="web_search",
                identity=mock_identity,
                db=db,
            )

        assert result.has_credit is False

    @pytest.mark.asyncio
    async def test_check_tool_credit_invalid_product(self, mock_identity):
        """check_tool_credit raises 400 for unknown product type."""
        from fastapi import HTTPException

        from app.api.tool_routes import check_tool_credit

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(
                side_effect=ValueError("Unknown product type: invalid")
            )

            with pytest.raises(HTTPException) as exc_info:
                await check_tool_credit(
                    product_type="invalid",
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_check_tool_credit_new_account_has_initial_free(self, mock_identity):
        """check_tool_credit returns initial free credits for new accounts."""
        from app.api.tool_routes import check_tool_credit

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(
                side_effect=ResourceNotFoundError("Account not found")
            )

            result = await check_tool_credit(
                product_type="web_search",
                identity=mock_identity,
                db=db,
            )

        # New accounts would get initial free credits
        assert result.has_credit is True
        assert result.product_type == "web_search"

    @pytest.mark.asyncio
    async def test_check_tool_credit_unknown_product_for_new_account(self, mock_identity):
        """check_tool_credit raises 400 for unknown product when account not found."""
        from fastapi import HTTPException

        from app.api.tool_routes import check_tool_credit

        db = AsyncMock()

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.get_balance = AsyncMock(
                side_effect=ResourceNotFoundError("Account not found")
            )

            with pytest.raises(HTTPException) as exc_info:
                await check_tool_credit(
                    product_type="unknown_product",
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 400


class TestToolChargeEndpoint:
    """Tests for tool charge endpoint."""

    @pytest.fixture
    def mock_identity(self):
        """Create mock account identity."""
        return AccountIdentity(
            oauth_provider="oauth:google",
            external_id="user@example.com",
            wa_id=None,
            tenant_id=None,
        )

    @pytest.fixture
    def mock_charge_result(self):
        """Create mock charge result."""
        return ProductChargeResult(
            success=True,
            used_free=True,
            used_paid=False,
            cost_minor=0,
            free_remaining=4,
            paid_credits=10,
            total_uses=51,
            usage_log_id=uuid4(),
        )

    @pytest.mark.asyncio
    async def test_charge_tool_usage_success(self, mock_identity, mock_charge_result):
        """charge_tool_usage charges and returns result."""
        from app.api.tool_routes import ToolChargeRequest, charge_tool_usage

        db = AsyncMock()

        request = ToolChargeRequest(
            product_type="web_search",
            idempotency_key="test-key-123",
            request_id="req-456",
        )

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.charge = AsyncMock(return_value=mock_charge_result)

            result = await charge_tool_usage(
                request=request,
                identity=mock_identity,
                db=db,
            )

        assert result.success is True
        assert result.used_free is True
        assert result.used_paid is False
        assert result.cost_minor == 0
        assert result.free_remaining == 4
        assert result.has_credit is True

    @pytest.mark.asyncio
    async def test_charge_tool_usage_paid_credits(self, mock_identity):
        """charge_tool_usage uses paid credits when no free credits."""
        from app.api.tool_routes import ToolChargeRequest, charge_tool_usage

        db = AsyncMock()

        mock_result = ProductChargeResult(
            success=True,
            used_free=False,
            used_paid=True,
            cost_minor=100,
            free_remaining=0,
            paid_credits=9,
            total_uses=52,
            usage_log_id=uuid4(),
        )

        request = ToolChargeRequest(product_type="web_search")

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.charge = AsyncMock(return_value=mock_result)

            result = await charge_tool_usage(
                request=request,
                identity=mock_identity,
                db=db,
            )

        assert result.used_paid is True
        assert result.cost_minor == 100

    @pytest.mark.asyncio
    async def test_charge_tool_usage_invalid_product(self, mock_identity):
        """charge_tool_usage raises 400 for unknown product."""
        from fastapi import HTTPException

        from app.api.tool_routes import ToolChargeRequest, charge_tool_usage

        db = AsyncMock()

        request = ToolChargeRequest(product_type="invalid")

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.charge = AsyncMock(side_effect=ValueError("Unknown product type"))

            with pytest.raises(HTTPException) as exc_info:
                await charge_tool_usage(
                    request=request,
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_charge_tool_usage_not_found(self, mock_identity):
        """charge_tool_usage raises 404 when resource not found."""
        from fastapi import HTTPException

        from app.api.tool_routes import ToolChargeRequest, charge_tool_usage

        db = AsyncMock()

        request = ToolChargeRequest(product_type="web_search")

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.charge = AsyncMock(
                side_effect=ResourceNotFoundError("Account creation failed")
            )

            with pytest.raises(HTTPException) as exc_info:
                await charge_tool_usage(
                    request=request,
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_charge_tool_usage_insufficient_credits(self, mock_identity):
        """charge_tool_usage raises 402 when no credits available."""
        from fastapi import HTTPException

        from app.api.tool_routes import ToolChargeRequest, charge_tool_usage

        db = AsyncMock()

        request = ToolChargeRequest(product_type="web_search")

        with patch("app.api.tool_routes.ProductInventoryService") as MockService:
            mock_service = MockService.return_value
            mock_service.charge = AsyncMock(
                side_effect=InsufficientCreditsError(balance=0, required=1)
            )

            with pytest.raises(HTTPException) as exc_info:
                await charge_tool_usage(
                    request=request,
                    identity=mock_identity,
                    db=db,
                )

            assert exc_info.value.status_code == 402


class TestToolModels:
    """Tests for tool route request/response models."""

    def test_tool_balance_response_model(self):
        """ToolBalanceResponse has correct fields."""
        from app.api.tool_routes import ToolBalanceResponse

        response = ToolBalanceResponse(
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            total_available=15,
            price_minor=100,
            total_uses=50,
        )
        assert response.product_type == "web_search"
        assert response.free_remaining == 5
        assert response.total_available == 15

    def test_all_tool_balances_response_model(self):
        """AllToolBalancesResponse has correct structure."""
        from app.api.tool_routes import AllToolBalancesResponse, ToolBalanceResponse

        balance = ToolBalanceResponse(
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            total_available=15,
            price_minor=100,
            total_uses=50,
        )
        response = AllToolBalancesResponse(balances=[balance])
        assert len(response.balances) == 1

    def test_tool_charge_request_model(self):
        """ToolChargeRequest accepts optional fields."""
        from app.api.tool_routes import ToolChargeRequest

        request = ToolChargeRequest(
            product_type="web_search",
            idempotency_key="key-123",
            request_id="req-456",
        )
        assert request.product_type == "web_search"
        assert request.idempotency_key == "key-123"

        # Optional fields can be None
        request2 = ToolChargeRequest(product_type="web_search")
        assert request2.idempotency_key is None
        assert request2.request_id is None

    def test_tool_charge_response_model(self):
        """ToolChargeResponse has correct fields."""
        from app.api.tool_routes import ToolChargeResponse

        response = ToolChargeResponse(
            success=True,
            has_credit=True,
            used_free=True,
            used_paid=False,
            cost_minor=0,
            free_remaining=4,
            paid_credits=10,
            total_uses=51,
        )
        assert response.success is True
        assert response.has_credit is True
        assert response.cost_minor == 0

    def test_tool_check_response_model(self):
        """ToolCheckResponse has correct fields."""
        from app.api.tool_routes import ToolCheckResponse

        response = ToolCheckResponse(
            has_credit=True,
            product_type="web_search",
            free_remaining=5,
            paid_credits=10,
            total_available=15,
        )
        assert response.has_credit is True
        assert response.product_type == "web_search"
