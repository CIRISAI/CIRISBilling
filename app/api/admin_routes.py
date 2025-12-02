"""
Admin API routes for managing billing system.

Protected by JWT authentication. Requires OAuth login.
Some routes require admin role, others allow viewer role.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.api.admin_dependencies import get_current_admin, require_admin_role
from app.db.models import Account, AdminUser, APIKey, Charge, Credit, LLMUsageLog, ProviderConfig
from app.db.session import get_read_db, get_write_db
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
    wa_id: str | None
    tenant_id: str | None
    customer_email: str | None
    balance_minor: int
    paid_credits: int
    free_uses_remaining: int
    total_uses: int
    currency: str
    plan_name: str
    status: str
    created_at: datetime
    last_charge_at: datetime | None
    last_credit_at: datetime | None
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
    expires_at: datetime | None
    last_used_at: datetime | None
    created_by_email: str | None


class APIKeyCreateRequest(BaseModel):
    """Request to create new API key."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name")
    environment: str = Field("live", pattern="^(test|live)$", description="test or live")
    permissions: list[str] = Field(
        default=["billing:read", "billing:write"],
        description="Permissions for this key",
    )
    expires_in_days: int | None = Field(
        None, ge=1, le=3650, description="Expiration in days (max 10 years)"
    )


class APIKeyCreateResponse(BaseModel):
    """Response after creating API key."""

    id: UUID
    name: str
    key_prefix: str
    plaintext_key: str = Field(..., description="SAVE THIS - It won't be shown again")
    environment: str
    permissions: list[str]
    expires_at: datetime | None
    created_at: datetime


class APIKeyRotateResponse(BaseModel):
    """Response after rotating API key."""

    id: UUID
    name: str
    new_key_prefix: str
    new_plaintext_key: str = Field(..., description="SAVE THIS - It won't be shown again")
    old_key_expires_at: datetime = Field(
        ..., description="Old key will work until this time (24h grace)"
    )


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
    config_data: dict[str, str]
    updated_at: datetime


class ProviderConfigUpdateRequest(BaseModel):
    """Update provider configuration."""

    is_enabled: bool | None = None
    config_data: dict[str, str] | None = None


# ============================================================================
# Margin Analytics Models
# ============================================================================


class UserMarginResponse(BaseModel):
    """Margin analytics for a single user."""

    account_id: UUID
    customer_email: str | None
    total_interactions: int  # Number of charged interactions
    total_revenue_cents: int  # Revenue from charges (100 cents per interaction)
    total_llm_cost_cents: int  # Actual LLM provider cost
    margin_cents: int  # Revenue - Cost
    margin_percent: float  # (Revenue - Cost) / Revenue * 100
    avg_llm_calls_per_interaction: float
    avg_tokens_per_interaction: int
    total_prompt_tokens: int
    total_completion_tokens: int
    models_used: list[str]
    first_interaction_at: datetime | None
    last_interaction_at: datetime | None


class UserMarginListResponse(BaseModel):
    """Paginated list of user margins."""

    users: list[UserMarginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    # Summary totals
    total_revenue_cents: int
    total_llm_cost_cents: int
    total_margin_cents: int
    overall_margin_percent: float


class DailyMarginResponse(BaseModel):
    """Daily margin analytics."""

    date: str  # YYYY-MM-DD
    total_interactions: int
    total_revenue_cents: int  # 100 cents per interaction
    total_llm_cost_cents: int  # Actual LLM cost
    margin_cents: int
    margin_percent: float
    unique_users: int
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    avg_cost_per_interaction_cents: float
    error_count: int
    fallback_count: int


class InteractionMarginResponse(BaseModel):
    """Margin details for a single interaction."""

    usage_log_id: UUID
    account_id: UUID
    customer_email: str | None
    interaction_id: str
    created_at: datetime
    revenue_cents: int  # 100 cents (1 credit)
    llm_cost_cents: int  # Actual cost
    margin_cents: int
    margin_percent: float
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    models_used: list[str]
    duration_ms: int
    error_count: int
    fallback_count: int


class InteractionMarginListResponse(BaseModel):
    """Paginated list of interaction margins."""

    interactions: list[InteractionMarginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarginOverviewResponse(BaseModel):
    """High-level margin overview."""

    model_config = {"protected_namespaces": ()}

    # Time period
    period_start: datetime
    period_end: datetime
    # Totals
    total_interactions: int
    total_revenue_cents: int
    total_llm_cost_cents: int
    total_margin_cents: int
    overall_margin_percent: float
    # Averages
    avg_cost_per_interaction_cents: float
    avg_revenue_per_user_cents: float
    avg_llm_calls_per_interaction: float
    avg_tokens_per_interaction: int
    # Counts
    unique_users: int
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_errors: int
    total_fallbacks: int
    # Model breakdown
    model_usage: dict[str, int]  # model_name -> count


# ============================================================================
# Users Management
# ============================================================================


@router.get("/users", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    status_filter: str | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search by email or external_id"),
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
            (Account.customer_email.ilike(search_pattern))
            | (Account.external_id.ilike(search_pattern))
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
                        else_=0,
                    )
                ),
                0,
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
                paid_credits=account.paid_credits,
                free_uses_remaining=account.free_uses_remaining,
                total_uses=account.total_uses,
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
                case((Charge.balance_before != Charge.balance_after, Charge.amount_minor), else_=0)
            ),
            0,
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
        paid_credits=account.paid_credits,
        free_uses_remaining=account.free_uses_remaining,
        total_uses=account.total_uses,
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
    environment: str | None = Query(None, pattern="^(test|live)$"),
    status_filter: str | None = Query(None, pattern="^(active|rotating|revoked)$"),
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

        # Calculate old key expiry (24-hour grace period from now)
        old_key_expires_at = datetime.now(UTC) + timedelta(hours=24)

        logger.info(
            "admin_api_key_rotated",
            admin_email=admin.email,
            key_id=str(key_id),
            new_key_id=str(rotation_data.key_id),
        )

        return APIKeyRotateResponse(
            id=rotation_data.key_id,
            name=rotation_data.name,
            new_key_prefix=rotation_data.key_prefix,
            new_plaintext_key=rotation_data.plaintext_key,
            old_key_expires_at=old_key_expires_at,
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
    now = datetime.now(UTC)
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
                case((Charge.balance_before != Charge.balance_after, Charge.amount_minor), else_=0)
            ),
            0,
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

    now = datetime.now(UTC)
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

        config.updated_at = datetime.now(UTC)
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


# ============================================================================
# Margin Analytics
# ============================================================================

# Revenue per interaction in cents (users pay 1 credit = $1.00 = 100 cents)
REVENUE_PER_INTERACTION_CENTS = 100


@router.get("/analytics/margin/overview", response_model=MarginOverviewResponse)
async def get_margin_overview(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> MarginOverviewResponse:
    """
    Get high-level margin overview for the specified period.

    Shows total revenue, costs, and margin across all users.

    Accessible by: admin, viewer
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    # Get aggregated usage stats
    usage_stmt = select(
        func.count(LLMUsageLog.id).label("total_interactions"),
        func.coalesce(func.sum(LLMUsageLog.actual_cost_cents), 0).label("total_cost"),
        func.coalesce(func.sum(LLMUsageLog.total_llm_calls), 0).label("total_llm_calls"),
        func.coalesce(func.sum(LLMUsageLog.total_prompt_tokens), 0).label("total_prompt_tokens"),
        func.coalesce(func.sum(LLMUsageLog.total_completion_tokens), 0).label(
            "total_completion_tokens"
        ),
        func.coalesce(func.sum(LLMUsageLog.error_count), 0).label("total_errors"),
        func.coalesce(func.sum(LLMUsageLog.fallback_count), 0).label("total_fallbacks"),
        func.count(func.distinct(LLMUsageLog.account_id)).label("unique_users"),
    ).where(LLMUsageLog.created_at >= period_start)

    usage_result = await db.execute(usage_stmt)
    usage_row = usage_result.one()

    total_interactions = usage_row.total_interactions
    total_llm_cost_cents = usage_row.total_cost
    total_revenue_cents = total_interactions * REVENUE_PER_INTERACTION_CENTS
    total_margin_cents = total_revenue_cents - total_llm_cost_cents

    # Calculate percentages and averages
    overall_margin_percent = (
        (total_margin_cents / total_revenue_cents * 100) if total_revenue_cents > 0 else 0.0
    )
    avg_cost_per_interaction = (
        total_llm_cost_cents / total_interactions if total_interactions > 0 else 0.0
    )
    avg_revenue_per_user = (
        total_revenue_cents / usage_row.unique_users if usage_row.unique_users > 0 else 0.0
    )
    avg_llm_calls = (
        usage_row.total_llm_calls / total_interactions if total_interactions > 0 else 0.0
    )
    total_tokens = usage_row.total_prompt_tokens + usage_row.total_completion_tokens
    avg_tokens = int(total_tokens / total_interactions) if total_interactions > 0 else 0

    # Get model usage breakdown
    model_stmt = (
        select(
            func.unnest(LLMUsageLog.models_used).label("model"),
            func.count().label("count"),
        )
        .where(LLMUsageLog.created_at >= period_start)
        .group_by(func.unnest(LLMUsageLog.models_used))
    )

    model_result = await db.execute(model_stmt)
    model_usage = {row.model: row.count for row in model_result.all()}

    logger.info(
        "admin_margin_overview",
        admin_email=admin.email,
        days=days,
        total_interactions=total_interactions,
        margin_percent=round(overall_margin_percent, 2),
    )

    return MarginOverviewResponse(
        period_start=period_start,
        period_end=now,
        total_interactions=total_interactions,
        total_revenue_cents=total_revenue_cents,
        total_llm_cost_cents=total_llm_cost_cents,
        total_margin_cents=total_margin_cents,
        overall_margin_percent=round(overall_margin_percent, 2),
        avg_cost_per_interaction_cents=round(avg_cost_per_interaction, 2),
        avg_revenue_per_user_cents=round(avg_revenue_per_user, 2),
        avg_llm_calls_per_interaction=round(avg_llm_calls, 2),
        avg_tokens_per_interaction=avg_tokens,
        unique_users=usage_row.unique_users,
        total_llm_calls=usage_row.total_llm_calls,
        total_prompt_tokens=usage_row.total_prompt_tokens,
        total_completion_tokens=usage_row.total_completion_tokens,
        total_errors=usage_row.total_errors,
        total_fallbacks=usage_row.total_fallbacks,
        model_usage=model_usage,
    )


@router.get("/analytics/margin/daily", response_model=list[DailyMarginResponse])
async def get_daily_margin(
    days: int = Query(30, ge=1, le=365, description="Number of days to retrieve"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> list[DailyMarginResponse]:
    """
    Get daily margin analytics for the specified period.

    Shows revenue, cost, and margin broken down by day.

    Accessible by: admin, viewer
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # Get daily usage aggregates
    daily_stmt = (
        select(
            func.date_trunc("day", LLMUsageLog.created_at).label("date"),
            func.count(LLMUsageLog.id).label("total_interactions"),
            func.coalesce(func.sum(LLMUsageLog.actual_cost_cents), 0).label("total_cost"),
            func.count(func.distinct(LLMUsageLog.account_id)).label("unique_users"),
            func.coalesce(func.sum(LLMUsageLog.total_llm_calls), 0).label("total_llm_calls"),
            func.coalesce(func.sum(LLMUsageLog.total_prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMUsageLog.total_completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.coalesce(func.sum(LLMUsageLog.error_count), 0).label("error_count"),
            func.coalesce(func.sum(LLMUsageLog.fallback_count), 0).label("fallback_count"),
        )
        .where(LLMUsageLog.created_at >= start_date)
        .group_by(func.date_trunc("day", LLMUsageLog.created_at))
        .order_by(func.date_trunc("day", LLMUsageLog.created_at))
    )

    result = await db.execute(daily_stmt)
    rows = result.all()

    # Build daily data with margin calculations
    daily_data = []
    for row in rows:
        revenue = row.total_interactions * REVENUE_PER_INTERACTION_CENTS
        cost = row.total_cost
        margin = revenue - cost
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0
        avg_cost = cost / row.total_interactions if row.total_interactions > 0 else 0.0

        daily_data.append(
            DailyMarginResponse(
                date=row.date.strftime("%Y-%m-%d"),
                total_interactions=row.total_interactions,
                total_revenue_cents=revenue,
                total_llm_cost_cents=cost,
                margin_cents=margin,
                margin_percent=round(margin_percent, 2),
                unique_users=row.unique_users,
                total_llm_calls=row.total_llm_calls,
                total_prompt_tokens=row.prompt_tokens,
                total_completion_tokens=row.completion_tokens,
                avg_cost_per_interaction_cents=round(avg_cost, 2),
                error_count=row.error_count,
                fallback_count=row.fallback_count,
            )
        )

    logger.info("admin_margin_daily", admin_email=admin.email, days=days, rows=len(daily_data))

    return daily_data


@router.get("/analytics/margin/users", response_model=UserMarginListResponse)
async def get_user_margins(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    sort_by: str = Query(
        "margin_cents",
        pattern="^(margin_cents|total_revenue_cents|total_llm_cost_cents|total_interactions)$",
        description="Sort field",
    ),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort order"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> UserMarginListResponse:
    """
    Get margin analytics per user.

    Shows each user's revenue, cost, and margin for the period.

    Accessible by: admin, viewer
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)
    offset = (page - 1) * page_size

    # Get per-user usage aggregates with account join
    user_stmt = (
        select(
            LLMUsageLog.account_id,
            Account.customer_email,
            func.count(LLMUsageLog.id).label("total_interactions"),
            func.coalesce(func.sum(LLMUsageLog.actual_cost_cents), 0).label("total_cost"),
            func.coalesce(func.sum(LLMUsageLog.total_llm_calls), 0).label("total_llm_calls"),
            func.coalesce(func.sum(LLMUsageLog.total_prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMUsageLog.total_completion_tokens), 0).label(
                "completion_tokens"
            ),
            func.min(LLMUsageLog.created_at).label("first_interaction"),
            func.max(LLMUsageLog.created_at).label("last_interaction"),
        )
        .join(Account, LLMUsageLog.account_id == Account.id)
        .where(LLMUsageLog.created_at >= start_date)
        .group_by(LLMUsageLog.account_id, Account.customer_email)
    )

    # Get total count
    count_subq = user_stmt.subquery()
    count_stmt = select(func.count()).select_from(count_subq)
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    # Apply sorting and pagination
    # We need to compute margin for sorting
    subq = user_stmt.subquery()
    sorted_stmt = select(subq).add_columns(
        (subq.c.total_interactions * REVENUE_PER_INTERACTION_CENTS).label("revenue"),
        (subq.c.total_interactions * REVENUE_PER_INTERACTION_CENTS - subq.c.total_cost).label(
            "margin"
        ),
    )

    # Apply sort
    if sort_by == "margin_cents":
        sort_col = subq.c.total_interactions * REVENUE_PER_INTERACTION_CENTS - subq.c.total_cost
    elif sort_by == "total_revenue_cents":
        sort_col = subq.c.total_interactions * REVENUE_PER_INTERACTION_CENTS
    elif sort_by == "total_llm_cost_cents":
        sort_col = subq.c.total_cost
    else:
        sort_col = subq.c.total_interactions

    if sort_order == "desc":
        sorted_stmt = sorted_stmt.order_by(sort_col.desc())
    else:
        sorted_stmt = sorted_stmt.order_by(sort_col.asc())

    sorted_stmt = sorted_stmt.offset(offset).limit(page_size)

    result = await db.execute(sorted_stmt)
    rows = result.all()

    # Build user margin list
    users = []
    total_revenue_all = 0
    total_cost_all = 0

    for row in rows:
        revenue = row.total_interactions * REVENUE_PER_INTERACTION_CENTS
        cost = row.total_cost
        margin = revenue - cost
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0
        avg_llm_calls = (
            row.total_llm_calls / row.total_interactions if row.total_interactions > 0 else 0.0
        )
        total_tokens = row.prompt_tokens + row.completion_tokens
        avg_tokens = int(total_tokens / row.total_interactions) if row.total_interactions > 0 else 0

        total_revenue_all += revenue
        total_cost_all += cost

        # Get models used for this user (separate query for array aggregation)
        models_stmt = (
            select(func.array_agg(func.distinct(func.unnest(LLMUsageLog.models_used))))
            .where(LLMUsageLog.account_id == row.account_id)
            .where(LLMUsageLog.created_at >= start_date)
        )
        models_result = await db.execute(models_stmt)
        models_used = models_result.scalar_one() or []

        users.append(
            UserMarginResponse(
                account_id=row.account_id,
                customer_email=row.customer_email,
                total_interactions=row.total_interactions,
                total_revenue_cents=revenue,
                total_llm_cost_cents=cost,
                margin_cents=margin,
                margin_percent=round(margin_percent, 2),
                avg_llm_calls_per_interaction=round(avg_llm_calls, 2),
                avg_tokens_per_interaction=avg_tokens,
                total_prompt_tokens=row.prompt_tokens,
                total_completion_tokens=row.completion_tokens,
                models_used=models_used,
                first_interaction_at=row.first_interaction,
                last_interaction_at=row.last_interaction,
            )
        )

    # Calculate overall summary
    total_margin_all = total_revenue_all - total_cost_all
    overall_margin_percent = (
        (total_margin_all / total_revenue_all * 100) if total_revenue_all > 0 else 0.0
    )
    total_pages = (total + page_size - 1) // page_size

    logger.info(
        "admin_margin_users",
        admin_email=admin.email,
        days=days,
        page=page,
        total=total,
    )

    return UserMarginListResponse(
        users=users,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        total_revenue_cents=total_revenue_all,
        total_llm_cost_cents=total_cost_all,
        total_margin_cents=total_margin_all,
        overall_margin_percent=round(overall_margin_percent, 2),
    )


@router.get("/analytics/margin/interactions", response_model=InteractionMarginListResponse)
async def get_interaction_margins(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    account_id: UUID | None = Query(None, description="Filter by account ID"),
    days: int = Query(7, ge=1, le=90, description="Number of days to retrieve"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> InteractionMarginListResponse:
    """
    Get detailed margin for each interaction.

    Shows revenue, cost, and margin per interaction with LLM details.

    Accessible by: admin, viewer
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)
    offset = (page - 1) * page_size

    # Build query
    stmt = (
        select(
            LLMUsageLog.id,
            LLMUsageLog.account_id,
            Account.customer_email,
            LLMUsageLog.interaction_id,
            LLMUsageLog.created_at,
            LLMUsageLog.actual_cost_cents,
            LLMUsageLog.total_llm_calls,
            LLMUsageLog.total_prompt_tokens,
            LLMUsageLog.total_completion_tokens,
            LLMUsageLog.models_used,
            LLMUsageLog.duration_ms,
            LLMUsageLog.error_count,
            LLMUsageLog.fallback_count,
        )
        .join(Account, LLMUsageLog.account_id == Account.id)
        .where(LLMUsageLog.created_at >= start_date)
    )

    if account_id:
        stmt = stmt.where(LLMUsageLog.account_id == account_id)

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    # Apply pagination and ordering (most recent first)
    stmt = stmt.order_by(LLMUsageLog.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(stmt)
    rows = result.all()

    # Build interaction list
    interactions = []
    for row in rows:
        revenue = REVENUE_PER_INTERACTION_CENTS
        cost = row.actual_cost_cents
        margin = revenue - cost
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0

        interactions.append(
            InteractionMarginResponse(
                usage_log_id=row.id,
                account_id=row.account_id,
                customer_email=row.customer_email,
                interaction_id=row.interaction_id,
                created_at=row.created_at,
                revenue_cents=revenue,
                llm_cost_cents=cost,
                margin_cents=margin,
                margin_percent=round(margin_percent, 2),
                total_llm_calls=row.total_llm_calls,
                total_prompt_tokens=row.total_prompt_tokens,
                total_completion_tokens=row.total_completion_tokens,
                models_used=row.models_used or [],
                duration_ms=row.duration_ms,
                error_count=row.error_count,
                fallback_count=row.fallback_count,
            )
        )

    total_pages = (total + page_size - 1) // page_size

    logger.info(
        "admin_margin_interactions",
        admin_email=admin.email,
        days=days,
        page=page,
        total=total,
        account_id=str(account_id) if account_id else None,
    )

    return InteractionMarginListResponse(
        interactions=interactions,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/analytics/margin/users/{account_id}", response_model=UserMarginResponse)
async def get_user_margin_detail(
    account_id: UUID,
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    db: AsyncSession = Depends(get_read_db),
    admin: AdminUser = Depends(get_current_admin),
) -> UserMarginResponse:
    """
    Get detailed margin analytics for a specific user.

    Accessible by: admin, viewer
    """
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # Get user's account
    account_stmt = select(Account).where(Account.id == account_id)
    account_result = await db.execute(account_stmt)
    account = account_result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found",
        )

    # Get usage stats for this user
    usage_stmt = select(
        func.count(LLMUsageLog.id).label("total_interactions"),
        func.coalesce(func.sum(LLMUsageLog.actual_cost_cents), 0).label("total_cost"),
        func.coalesce(func.sum(LLMUsageLog.total_llm_calls), 0).label("total_llm_calls"),
        func.coalesce(func.sum(LLMUsageLog.total_prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LLMUsageLog.total_completion_tokens), 0).label("completion_tokens"),
        func.min(LLMUsageLog.created_at).label("first_interaction"),
        func.max(LLMUsageLog.created_at).label("last_interaction"),
    ).where(
        LLMUsageLog.account_id == account_id,
        LLMUsageLog.created_at >= start_date,
    )

    usage_result = await db.execute(usage_stmt)
    usage_row = usage_result.one()

    # Get models used
    models_stmt = (
        select(func.array_agg(func.distinct(func.unnest(LLMUsageLog.models_used))))
        .where(LLMUsageLog.account_id == account_id)
        .where(LLMUsageLog.created_at >= start_date)
    )
    models_result = await db.execute(models_stmt)
    models_used = models_result.scalar_one() or []

    # Calculate metrics
    revenue = usage_row.total_interactions * REVENUE_PER_INTERACTION_CENTS
    cost = usage_row.total_cost
    margin = revenue - cost
    margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0
    avg_llm_calls = (
        usage_row.total_llm_calls / usage_row.total_interactions
        if usage_row.total_interactions > 0
        else 0.0
    )
    total_tokens = usage_row.prompt_tokens + usage_row.completion_tokens
    avg_tokens = (
        int(total_tokens / usage_row.total_interactions) if usage_row.total_interactions > 0 else 0
    )

    logger.info(
        "admin_margin_user_detail",
        admin_email=admin.email,
        account_id=str(account_id),
        days=days,
    )

    return UserMarginResponse(
        account_id=account_id,
        customer_email=account.customer_email,
        total_interactions=usage_row.total_interactions,
        total_revenue_cents=revenue,
        total_llm_cost_cents=cost,
        margin_cents=margin,
        margin_percent=round(margin_percent, 2),
        avg_llm_calls_per_interaction=round(avg_llm_calls, 2),
        avg_tokens_per_interaction=avg_tokens,
        total_prompt_tokens=usage_row.prompt_tokens,
        total_completion_tokens=usage_row.completion_tokens,
        models_used=models_used,
        first_interaction_at=usage_row.first_interaction,
        last_interaction_at=usage_row.last_interaction,
    )
