"""
Tool API routes - Endpoints for tool credit management.

These endpoints handle web search, image generation, and other
tool-specific credits separately from main LLM usage.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.api.dependencies import get_validated_identity
from app.db.session import get_write_db
from app.exceptions import InsufficientCreditsError, ResourceNotFoundError
from app.models.domain import AccountIdentity
from app.services.product_inventory import ProductInventoryService

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/tools", tags=["tools"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ToolBalanceResponse(BaseModel):
    """Balance for a single tool/product."""

    product_type: str = Field(..., description="Product type (e.g., 'web_search')")
    free_remaining: int = Field(..., description="Free credits remaining")
    paid_credits: int = Field(..., description="Tool-specific paid credits available")
    main_pool_credits: int = Field(
        default=0, description="Credits available from main account pool (fallback)"
    )
    total_available: int = Field(..., description="Total credits (free + paid + main_pool)")
    price_minor: int = Field(..., description="Price per use in cents")
    total_uses: int = Field(..., description="Lifetime usage count")


class AllToolBalancesResponse(BaseModel):
    """Balances for all tool types."""

    balances: list[ToolBalanceResponse]


class ToolChargeRequest(BaseModel):
    """Request to charge for tool usage."""

    product_type: str = Field(..., description="Product type (e.g., 'web_search')")
    idempotency_key: str | None = Field(None, description="Unique key to prevent duplicate charges")
    request_id: str | None = Field(None, description="Request ID for tracking")


class ToolChargeResponse(BaseModel):
    """Response after charging for tool usage."""

    success: bool = Field(..., description="Whether charge succeeded")
    has_credit: bool = Field(..., description="Whether user still has credit")
    used_free: bool = Field(..., description="Whether free credit was used")
    used_paid: bool = Field(..., description="Whether paid credit was used")
    cost_minor: int = Field(..., description="Cost charged in cents (0 if free)")
    free_remaining: int = Field(..., description="Free credits remaining after charge")
    paid_credits: int = Field(..., description="Paid credits remaining after charge")
    total_uses: int = Field(..., description="Total lifetime uses")


class ToolCheckResponse(BaseModel):
    """Response for credit check."""

    has_credit: bool = Field(..., description="Whether user has any credit")
    product_type: str = Field(..., description="Product type checked")
    free_remaining: int = Field(..., description="Free credits remaining")
    paid_credits: int = Field(..., description="Tool-specific paid credits available")
    main_pool_credits: int = Field(
        default=0, description="Credits available from main account pool (fallback)"
    )
    total_available: int = Field(..., description="Total credits available")


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/balance/{product_type}", response_model=ToolBalanceResponse)
async def get_tool_balance(
    product_type: str,
    identity: Annotated[AccountIdentity, Depends(get_validated_identity)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> ToolBalanceResponse:
    """
    Get balance for a specific tool/product.

    Returns the number of free and paid credits available,
    plus pricing and usage statistics.
    """
    service = ProductInventoryService(db)

    try:
        balance = await service.get_balance(identity, product_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ResourceNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return ToolBalanceResponse(
        product_type=balance.product_type,
        free_remaining=balance.free_remaining,
        paid_credits=balance.paid_credits,
        main_pool_credits=balance.main_pool_credits,
        total_available=balance.total_available,
        price_minor=balance.price_minor,
        total_uses=balance.total_uses,
    )


@router.get("/balance", response_model=AllToolBalancesResponse)
async def get_all_tool_balances(
    identity: Annotated[AccountIdentity, Depends(get_validated_identity)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> AllToolBalancesResponse:
    """
    Get balances for all tool/product types.

    Returns credits for all configured products.
    """
    service = ProductInventoryService(db)

    try:
        balances = await service.get_all_balances(identity)
    except ResourceNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e

    return AllToolBalancesResponse(
        balances=[
            ToolBalanceResponse(
                product_type=b.product_type,
                free_remaining=b.free_remaining,
                paid_credits=b.paid_credits,
                main_pool_credits=b.main_pool_credits,
                total_available=b.total_available,
                price_minor=b.price_minor,
                total_uses=b.total_uses,
            )
            for b in balances
        ]
    )


@router.get("/check/{product_type}", response_model=ToolCheckResponse)
async def check_tool_credit(
    product_type: str,
    identity: Annotated[AccountIdentity, Depends(get_validated_identity)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> ToolCheckResponse:
    """
    Check if user has credit for a specific tool.

    Quick check endpoint - returns has_credit boolean.
    Use this before making tool API calls.
    """
    service = ProductInventoryService(db)

    try:
        balance = await service.get_balance(identity, product_type)
        has_credit = balance.total_available > 0
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ResourceNotFoundError:
        # Account doesn't exist - they'd get free credits on first charge
        # For check, return True since they'd have initial credits
        from app.services.product_inventory import PRODUCT_CONFIGS

        if product_type not in PRODUCT_CONFIGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown product type: {product_type}",
            )
        config = PRODUCT_CONFIGS[product_type]
        return ToolCheckResponse(
            has_credit=config.free_initial > 0,
            product_type=product_type,
            free_remaining=config.free_initial,
            paid_credits=0,
            main_pool_credits=0,  # Unknown for new account
            total_available=config.free_initial,
        )

    return ToolCheckResponse(
        has_credit=has_credit,
        product_type=product_type,
        free_remaining=balance.free_remaining,
        paid_credits=balance.paid_credits,
        main_pool_credits=balance.main_pool_credits,
        total_available=balance.total_available,
    )


@router.post("/charge", response_model=ToolChargeResponse)
async def charge_tool_usage(
    request: ToolChargeRequest,
    identity: Annotated[AccountIdentity, Depends(get_validated_identity)],
    db: Annotated[AsyncSession, Depends(get_write_db)],
) -> ToolChargeResponse:
    """
    Charge for tool usage.

    Deducts one credit from the user's tool balance.
    Uses free credits first, then paid credits.

    Returns 402 Payment Required if no credits available.
    Supports idempotency via idempotency_key.
    """
    service = ProductInventoryService(db)

    try:
        result = await service.charge(
            identity=identity,
            product_type=request.product_type,
            idempotency_key=request.idempotency_key,
            request_id=request.request_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ResourceNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except InsufficientCreditsError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        ) from e

    return ToolChargeResponse(
        success=result.success,
        has_credit=(result.free_remaining + result.paid_credits) > 0,
        used_free=result.used_free,
        used_paid=result.used_paid,
        cost_minor=result.cost_minor,
        free_remaining=result.free_remaining,
        paid_credits=result.paid_credits,
        total_uses=result.total_uses,
    )
