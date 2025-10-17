"""
Admin API routes for managing billing system.

Protected by JWT authentication. Requires OAuth login.
Some routes require admin role, others allow viewer role.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.api.admin_dependencies import get_current_admin, require_admin_role
from app.db.session import get_read_db, get_write_db
from app.db.models import APIKey, Account, AdminUser, Charge, Credit, ProviderConfig
from app.services.api_key import APIKeyService

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# ============================================================================
# Request/Response Models
# ============================================================================


class UserResponse(BaseModel):
    """Admin user response."""

    account_id: UUID
    oauth_provider: str
    external_id: str
    wa_id: Optional[str]
    tenant_id: Optional[str]
    customer_email: Optional[str]
    balance_minor: int
    currency: str
    plan_name: str
    status: str
    created_at: datetime
    last_charge_at: Optional[datetime]
    last_credit_at: Optional[datetime]
    total_charged: int
    total_credited: int
    charge_count: int
    credit_count: int


class UserListResponse(BaseModel):
    """Paginated user list response."""

    users: list[UserResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class APIKeyResponse(BaseModel):
    """API key response (without sensitive data)."""

    id: UUID
    name: str
    key_prefix: str
    environment: str
    permissions: list[str]
    status: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_by_email: Optional[str]


class APIKeyCreateRequest(BaseModel):
    """Request to create new API key."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name")
    environment: str = Field("live", pattern="^(test|live)$", description="test or live")
    permissions: list[str] = Field(
        default=["billing:read", "billing:write"],
        description="Permissions for this key",
    )
    expires_in_days: Optional[int] = Field(None, ge=1, le=3650, description="Expiration in days (max 10 years)")


class APIKeyCreateResponse(BaseModel):
    """Response after creating API key."""

    id: UUID
    name: str
    key_prefix: str
    plaintext_key: str = Field(..., description="SAVE THIS - It won't be shown again")
    environment: str
    permissions: list[str]
    expires_at: Optional[datetime]
    created_at: datetime


class APIKeyRotateResponse(BaseModel):
    """Response after rotating API key."""

    id: UUID
    name: str
    new_key_prefix: str
    new_plaintext_key: str = Field(..., description="SAVE THIS - It won't be shown again")
    old_key_expires_at: datetime = Field(..., description="Old key will work until this time (24h grace)")


class AnalyticsOverviewResponse(BaseModel):
    """Dashboard overview analytics."""

    total_users: int
    active_users: int
    total_api_keys: int
    active_api_keys: int
    total_balance_minor: int
    total_charged_all_time: int
    total_credited_all_time: int
    charges_last_24h: int
    credits_last_24h: int
    charges_last_7d: int
    credits_last_7d: int


class DailyAnalyticsResponse(BaseModel):
    """Daily aggregated analytics."""

    date: str
    charges: int
    credits: int
    charge_amount: int
    credit_amount: int
    unique_users: int


class ProviderConfigResponse(BaseModel):
    """Provider configuration response."""

    id: UUID
    provider_name: str
    is_enabled: bool
    config_data: dict
    updated_at: datetime


class ProviderConfigUpdateRequest(BaseModel):
    """Update provider configuration."""

    is_enabled: Optional[bool] = None
    config_data: Optional[dict] = None


# ============================================================================
# Users Management
# ============================================================================


@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search by email or external_id"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),  # Both admin and viewer can view
) -> UserListResponse:
    """
    List all billing users with pagination.

    Accessible by: admin, viewer
    """
    offset = (page - 1) * page_size

    # Build query
    stmt = select(Account)

    # Apply filters
    if status_filter:
        stmt = stmt.where(Account.status == status_filter)

    if search:
        search_pattern = f"%{search}%"
        stmt = stmt.where(
            (Account.email.ilike(search_pattern)) | (Account.external_id.ilike(search_pattern))
        )

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Get paginated results
    stmt = stmt.order_by(Account.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    accounts = result.scalars().all()

    # Get aggregated charge/credit data for each account
    users = []
    for account in accounts:
        # Get charge stats
        # Only count paid charges (where balance changed) - free uses don't count as "charged"
        charge_stmt = select(
            func.count(Charge.id).label("charge_count"),
            func.coalesce(
                func.sum(
                    case(
                        (Charge.balance_before != Charge.balance_after, Charge.amount_minor),
                        else_=0
                    )
                ),
                0
            ).label("total_charged"),
            func.max(Charge.created_at).label("last_charge_at"),
        ).where(Charge.account_id == account.id)

        charge_result = await db.execute(charge_stmt)
        charge_row = charge_result.one()

        # Get credit stats
        credit_stmt = select(
            func.count(Credit.id).label("credit_count"),
            func.coalesce(func.sum(Credit.amount_minor), 0).label("total_credited"),
            func.max(Credit.created_at).label("last_credit_at"),
        ).where(Credit.account_id == account.id)

        credit_result = await db.execute(credit_stmt)
        credit_row = credit_result.one()

        users.append(
            UserResponse(
                account_id=account.id,
                oauth_provider=account.oauth_provider,
                external_id=account.external_id,
                wa_id=account.wa_id,
                tenant_id=account.tenant_id,
                customer_email=account.customer_email,
                balance_minor=account.balance_minor,
                currency=account.currency,
                plan_name=account.plan_name,
                status=account.status,
                created_at=account.created_at,
                last_charge_at=charge_row.last_charge_at,
                last_credit_at=credit_row.last_credit_at,
                total_charged=charge_row.total_charged,
                total_credited=credit_row.total_credited,
                charge_count=charge_row.charge_count,
                credit_count=credit_row.credit_count,
            )
        )

    total_pages = (total + page_size - 1) // page_size

    logger.info(
        "admin_list_users",
        admin_email=admin.email,
        page=page,
        page_size=page_size,
        total=total,
    )

    return UserListResponse(
        users=users,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/users/{account_id}", response_model=UserResponse)
async def get_user(
    account_id: UUID,
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> UserResponse:
    """
    Get detailed user information.

    Accessible by: admin, viewer
    """
    stmt = select(Account).where(Account.id == account_id)
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {account_id} not found",
        )

    # Get charge stats
    # Only count paid charges (where balance changed) - free uses don't count as "charged"
    charge_stmt = select(
        func.count(Charge.id).label("charge_count"),
        func.coalesce(
            func.sum(
                case(
                    (Charge.balance_before != Charge.balance_after, Charge.amount_minor),
                    else_=0
                )
            ),
            0
        ).label("total_charged"),
        func.max(Charge.created_at).label("last_charge_at"),
    ).where(Charge.account_id == account.id)

    charge_result = await db.execute(charge_stmt)
    charge_row = charge_result.one()

    # Get credit stats
    credit_stmt = select(
        func.count(Credit.id).label("credit_count"),
        func.coalesce(func.sum(Credit.amount_minor), 0).label("total_credited"),
        func.max(Credit.created_at).label("last_credit_at"),
    ).where(Credit.account_id == account.id)

    credit_result = await db.execute(credit_stmt)
    credit_row = credit_result.one()

    logger.info(
        "admin_get_user",
        admin_email=admin.email,
        account_id=str(account_id),
    )

    return UserResponse(
        account_id=account.id,
        oauth_provider=account.oauth_provider,
        external_id=account.external_id,
        wa_id=account.wa_id,
        tenant_id=account.tenant_id,
        customer_email=account.customer_email,
        balance_minor=account.balance_minor,
        currency=account.currency,
        plan_name=account.plan_name,
        status=account.status,
        created_at=account.created_at,
        last_charge_at=charge_row.last_charge_at,
        last_credit_at=credit_row.last_credit_at,
        total_charged=charge_row.total_charged,
        total_credited=credit_row.total_credited,
        charge_count=charge_row.charge_count,
        credit_count=credit_row.credit_count,
    )


# ============================================================================
# API Keys Management
# ============================================================================


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    environment: Optional[str] = Query(None, pattern="^(test|live)$"),
    status_filter: Optional[str] = Query(None, pattern="^(active|rotating|revoked)$"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[APIKeyResponse]:
    """
    List all API keys.

    Accessible by: admin, viewer
    """
    stmt = select(APIKey).order_by(APIKey.created_at.desc())

    if environment:
        stmt = stmt.where(APIKey.environment == environment)

    if status_filter:
        stmt = stmt.where(APIKey.status == status_filter)

    result = await db.execute(stmt)
    api_keys = result.scalars().all()

    logger.info(
        "admin_list_api_keys",
        admin_email=admin.email,
        count=len(api_keys),
    )

    return [
        APIKeyResponse(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            environment=key.environment,
            permissions=key.permissions,
            status=key.status,
            created_at=key.created_at,
            expires_at=key.expires_at,
            last_used_at=key.last_used_at,
            created_by_email=key.created_by.email if key.created_by else None,
        )
        for key in api_keys
    ]


@router.post("/api-keys", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    request: APIKeyCreateRequest,
    db: AsyncSession = Depends(get_write_db),
    admin: AdminUser = Depends(require_admin_role),  # Admin only
) -> APIKeyCreateResponse:
    """
    Create a new API key.

    Accessible by: admin only
    """
    api_key_service = APIKeyService(db)

    try:
        api_key_data = await api_key_service.create_api_key(
            name=request.name,
            created_by=admin.id,  # Service expects 'created_by' parameter
            environment=request.environment,
            permissions=request.permissions,
            expires_in_days=request.expires_in_days,
        )

        logger.info(
            "admin_api_key_created",
            admin_email=admin.email,
            key_name=request.name,
            key_id=str(api_key_data.key_id),
            environment=request.environment,
        )

        return APIKeyCreateResponse(
            id=api_key_data.key_id,
            name=api_key_data.name,
            key_prefix=api_key_data.key_prefix,
            plaintext_key=api_key_data.plaintext_key,
            environment=api_key_data.environment,
            permissions=api_key_data.permissions,
            expires_at=api_key_data.expires_at,
            created_at=api_key_data.created_at,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_write_db),
    admin: AdminUser = Depends(require_admin_role),  # Admin only
) -> None:
    """
    Revoke an API key immediately.

    Accessible by: admin only
    """
    api_key_service = APIKeyService(db)

    try:
        await api_key_service.revoke_api_key(key_id)

        logger.info(
            "admin_api_key_revoked",
            admin_email=admin.email,
            key_id=str(key_id),
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.post("/api-keys/{key_id}/rotate", response_model=APIKeyRotateResponse)
async def rotate_api_key(
    key_id: UUID,
    db: AsyncSession = Depends(get_write_db),
    admin: AdminUser = Depends(require_admin_role),  # Admin only
) -> APIKeyRotateResponse:
    """
    Rotate an API key (24-hour grace period).

    Old key will continue working for 24 hours.

    Accessible by: admin only
    """
    api_key_service = APIKeyService(db)

    try:
        rotation_data = await api_key_service.rotate_api_key(key_id)

        logger.info(
            "admin_api_key_rotated",
            admin_email=admin.email,
            key_id=str(key_id),
            new_key_id=str(rotation_data.new_key_id),
        )

        return APIKeyRotateResponse(
            id=rotation_data.new_key_id,
            name=rotation_data.name,
            new_key_prefix=rotation_data.new_key_prefix,
            new_plaintext_key=rotation_data.new_plaintext_key,
            old_key_expires_at=rotation_data.old_key_expires_at,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


# ============================================================================
# Analytics
# ============================================================================


@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
async def get_analytics_overview(
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> AnalyticsOverviewResponse:
    """
    Get dashboard overview analytics.

    Accessible by: admin, viewer
    """
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)

    # Total users
    total_users_stmt = select(func.count(Account.id))
    total_users_result = await db.execute(total_users_stmt)
    total_users = total_users_result.scalar_one()

    # Active users (status = active)
    active_users_stmt = select(func.count(Account.id)).where(Account.status == "active")
    active_users_result = await db.execute(active_users_stmt)
    active_users = active_users_result.scalar_one()

    # Total API keys
    total_keys_stmt = select(func.count(APIKey.id))
    total_keys_result = await db.execute(total_keys_stmt)
    total_api_keys = total_keys_result.scalar_one()

    # Active API keys
    active_keys_stmt = select(func.count(APIKey.id)).where(APIKey.status == "active")
    active_keys_result = await db.execute(active_keys_stmt)
    active_api_keys = active_keys_result.scalar_one()

    # Total balance across all accounts
    total_balance_stmt = select(func.coalesce(func.sum(Account.balance_minor), 0))
    total_balance_result = await db.execute(total_balance_stmt)
    total_balance_minor = total_balance_result.scalar_one()

    # All-time charges and credits
    # Only count paid charges (where balance changed) - free uses don't count as "charged"
    total_charged_stmt = select(
        func.coalesce(
            func.sum(
                case(
                    (Charge.balance_before != Charge.balance_after, Charge.amount_minor),
                    else_=0
                )
            ),
            0
        )
    )
    total_charged_result = await db.execute(total_charged_stmt)
    total_charged_all_time = total_charged_result.scalar_one()

    total_credited_stmt = select(func.coalesce(func.sum(Credit.amount_minor), 0))
    total_credited_result = await db.execute(total_credited_stmt)
    total_credited_all_time = total_credited_result.scalar_one()

    # Last 24h charges and credits
    charges_24h_stmt = select(func.count(Charge.id)).where(Charge.created_at >= last_24h)
    charges_24h_result = await db.execute(charges_24h_stmt)
    charges_last_24h = charges_24h_result.scalar_one()

    credits_24h_stmt = select(func.count(Credit.id)).where(Credit.created_at >= last_24h)
    credits_24h_result = await db.execute(credits_24h_stmt)
    credits_last_24h = credits_24h_result.scalar_one()

    # Last 7d charges and credits
    charges_7d_stmt = select(func.count(Charge.id)).where(Charge.created_at >= last_7d)
    charges_7d_result = await db.execute(charges_7d_stmt)
    charges_last_7d = charges_7d_result.scalar_one()

    credits_7d_stmt = select(func.count(Credit.id)).where(Credit.created_at >= last_7d)
    credits_7d_result = await db.execute(credits_7d_stmt)
    credits_last_7d = credits_7d_result.scalar_one()

    logger.info("admin_analytics_overview", admin_email=admin.email)

    return AnalyticsOverviewResponse(
        total_users=total_users,
        active_users=active_users,
        total_api_keys=total_api_keys,
        active_api_keys=active_api_keys,
        total_balance_minor=total_balance_minor,
        total_charged_all_time=total_charged_all_time,
        total_credited_all_time=total_credited_all_time,
        charges_last_24h=charges_last_24h,
        credits_last_24h=credits_last_24h,
        charges_last_7d=charges_last_7d,
        credits_last_7d=credits_last_7d,
    )


@router.get("/analytics/daily", response_model=list[DailyAnalyticsResponse])
async def get_daily_analytics(
    days: int = Query(30, ge=1, le=365, description="Number of days to retrieve"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[DailyAnalyticsResponse]:
    """
    Get daily aggregated analytics.

    Accessible by: admin, viewer
    """
    # This is a simplified version - in production you'd want pre-aggregated data
    # in a separate analytics table for performance

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get daily charge aggregates
    charge_stmt = (
        select(
            func.date_trunc("day", Charge.created_at).label("date"),
            func.count(Charge.id).label("charges"),
            func.sum(Charge.amount_minor).label("charge_amount"),
            func.count(func.distinct(Charge.account_id)).label("unique_users"),
        )
        .where(Charge.created_at >= start_date)
        .group_by(func.date_trunc("day", Charge.created_at))
    )

    charge_result = await db.execute(charge_stmt)
    charge_rows = {row.date: row for row in charge_result.all()}

    # Get daily credit aggregates
    credit_stmt = (
        select(
            func.date_trunc("day", Credit.created_at).label("date"),
            func.count(Credit.id).label("credits"),
            func.sum(Credit.amount_minor).label("credit_amount"),
        )
        .where(Credit.created_at >= start_date)
        .group_by(func.date_trunc("day", Credit.created_at))
    )

    credit_result = await db.execute(credit_stmt)
    credit_rows = {row.date: row for row in credit_result.all()}

    # Combine data
    daily_data = []
    current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    while current_date <= now:
        charge_row = charge_rows.get(current_date)
        credit_row = credit_rows.get(current_date)

        daily_data.append(
            DailyAnalyticsResponse(
                date=current_date.strftime("%Y-%m-%d"),
                charges=charge_row.charges if charge_row else 0,
                credits=credit_row.credits if credit_row else 0,
                charge_amount=charge_row.charge_amount if charge_row else 0,
                credit_amount=credit_row.credit_amount if credit_row else 0,
                unique_users=charge_row.unique_users if charge_row else 0,
            )
        )

        current_date += timedelta(days=1)

    logger.info("admin_analytics_daily", admin_email=admin.email, days=days)

    return daily_data


# ============================================================================
# Configuration
# ============================================================================


@router.get("/config/providers", response_model=list[ProviderConfigResponse])
async def list_provider_configs(
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[ProviderConfigResponse]:
    """
    List all provider configurations.

    Accessible by: admin, viewer
    """
    stmt = select(ProviderConfig).order_by(ProviderConfig.provider_type)
    result = await db.execute(stmt)
    configs = result.scalars().all()

    logger.info("admin_list_provider_configs", admin_email=admin.email)

    return [
        ProviderConfigResponse(
            id=config.id,
            provider_name=config.provider_type,
            is_enabled=config.is_active,
            config_data=config.config_data,
            updated_at=config.updated_at,
        )
        for config in configs
    ]


@router.put("/config/providers/{provider_name}", response_model=ProviderConfigResponse)
async def update_provider_config(
    provider_name: str,
    request: ProviderConfigUpdateRequest,
    db: AsyncSession = Depends(get_write_db),
    admin: AdminUser = Depends(require_admin_role),  # Admin only
) -> ProviderConfigResponse:
    """
    Update or create provider configuration.

    Accessible by: admin only
    """
    stmt = select(ProviderConfig).where(ProviderConfig.provider_type == provider_name)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()

    if not config:
        # Create new provider config if it doesn't exist
        config = ProviderConfig(
            provider_type=provider_name,
            is_active=request.is_enabled if request.is_enabled is not None else True,
            config_data=request.config_data or {},
            updated_by=admin.id,
        )
        db.add(config)
    else:
        # Update existing config
        if request.is_enabled is not None:
            config.is_active = request.is_enabled

        if request.config_data is not None:
            config.config_data = request.config_data

        config.updated_at = datetime.now(timezone.utc)
        config.updated_by = admin.id

    await db.commit()
    await db.refresh(config)

    logger.info(
        "admin_update_provider_config",
        admin_email=admin.email,
        provider_name=provider_name,
    )

    return ProviderConfigResponse(
        id=config.id,
        provider_name=config.provider_type,
        is_enabled=config.is_active,
        config_data=config.config_data,
        updated_at=config.updated_at,
    )
