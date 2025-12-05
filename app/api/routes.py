"""
API Routes - FastAPI endpoints for billing operations.

NO DICTIONARIES - All requests/responses use Pydantic models.
"""

from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    CombinedAuth,
    UserIdentity,
    get_user_from_google_token,
    require_permission,
    require_permission_or_jwt,
)
from app.db.session import get_read_db, get_write_db
from app.exceptions import (
    AccountClosedError,
    AccountNotFoundError,
    AccountSuspendedError,
    DataIntegrityError,
    IdempotencyConflictError,
    InsufficientCreditsError,
    WriteVerificationError,
)
from app.models.api import (
    AccountResponse,
    AddCreditsRequest,
    ChargeMetadata,
    ChargeResponse,
    CreateAccountRequest,
    CreateChargeRequest,
    CreditCheckRequest,
    CreditCheckResponse,
    CreditResponse,
    GooglePlayVerifyRequest,
    GooglePlayVerifyResponse,
    HealthResponse,
    LiteLLMUsageLogRequest,
    LiteLLMUsageLogResponse,
    PurchaseRequest,
    PurchaseResponse,
    TransactionItem,
    TransactionListResponse,
    UserGooglePlayVerifyRequest,
    UserGooglePlayVerifyResponse,
)
from app.models.domain import AccountIdentity, ChargeIntent, CreditIntent
from app.services.api_key import APIKeyData
from app.services.billing import BillingService

router = APIRouter()


@router.post("/v1/billing/credits/check", response_model=CreditCheckResponse)
async def check_credit(
    request: CreditCheckRequest,
    db: AsyncSession = Depends(get_write_db),
    auth: CombinedAuth = Depends(require_permission_or_jwt("billing:read")),
) -> CreditCheckResponse:
    """
    Check if account has sufficient credits.

    Auto-creates new accounts with free credits on first check.
    Updates account metadata if provided for existing accounts.
    Write operation - requires primary database.

    Auth: API key with billing:read permission OR Bearer {google_id_token}

    When using JWT auth, oauth_provider and external_id are extracted from the token.
    """
    service = BillingService(db)

    # If JWT auth, use identity from token; otherwise use request body
    if auth.auth_type == "jwt" and auth.user:
        identity = AccountIdentity(
            oauth_provider=auth.user.oauth_provider,
            external_id=auth.user.external_id,
            wa_id=request.wa_id if request else None,
            tenant_id=request.tenant_id if request else None,
        )
        # Use JWT token values as fallback for email/name
        customer_email = request.customer_email or auth.user.email
        display_name = request.display_name or auth.user.name
    else:
        identity = AccountIdentity(
            oauth_provider=request.oauth_provider,
            external_id=request.external_id,
            wa_id=request.wa_id,
            tenant_id=request.tenant_id,
        )
        customer_email = request.customer_email
        display_name = request.display_name

    # First check credit (creates account if needed)
    result = await service.check_credit(
        identity,
        request.context,
        customer_email=customer_email,
        marketing_opt_in=request.marketing_opt_in,
        marketing_opt_in_source=request.marketing_opt_in_source,
        user_role=request.user_role,
        agent_id=request.agent_id,
    )

    # Update account metadata if provided (for existing accounts)
    # Always call update to ensure metadata is synced on every request
    await service.update_account_metadata(
        identity=identity,
        customer_email=customer_email,
        display_name=display_name,
        marketing_opt_in=request.marketing_opt_in if request.marketing_opt_in else None,
        marketing_opt_in_source=request.marketing_opt_in_source,
        user_role=request.user_role,
        agent_id=request.agent_id,
    )

    return result


@router.post(
    "/v1/billing/charges",
    response_model=ChargeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_charge(
    request: CreateChargeRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> ChargeResponse:
    """
    Create a charge (deduct credits from account).

    Write operation - requires primary database.
    Requires: API key with billing:write permission.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=request.wa_id,
        tenant_id=request.tenant_id,
    )

    # Update account metadata on every charge request
    await service.update_account_metadata(
        identity=identity,
        customer_email=request.customer_email,
        display_name=request.display_name,
        marketing_opt_in=request.marketing_opt_in if request.marketing_opt_in else None,
        marketing_opt_in_source=request.marketing_opt_in_source,
        user_role=request.user_role,
        agent_id=request.agent_id,
    )

    intent = ChargeIntent(
        account_identity=identity,
        amount_minor=request.amount_minor,
        currency=request.currency,
        description=request.description,
        metadata=request.metadata,
        idempotency_key=request.idempotency_key,
    )

    try:
        charge_data = await service.create_charge(intent)

        return ChargeResponse(
            charge_id=charge_data.charge_id,
            account_id=charge_data.account_id,
            amount_minor=charge_data.amount_minor,
            currency=charge_data.currency,
            balance_after=charge_data.balance_after,
            created_at=charge_data.created_at.isoformat(),
            description=charge_data.description,
            metadata=charge_data.metadata,
        )

    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc

    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient credits. Balance: {exc.balance}, Required: {exc.required}",
        ) from exc

    except AccountSuspendedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account suspended: {exc.reason}",
        ) from exc

    except AccountClosedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is closed",
        ) from exc

    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Charge already exists",
            headers={"X-Existing-Charge-ID": str(exc.existing_id)},
        ) from exc

    except (WriteVerificationError, DataIntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database integrity error",
        ) from exc


@router.post(
    "/v1/billing/credits",
    response_model=CreditResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_credits(
    request: AddCreditsRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> CreditResponse:
    """
    Add credits to account (top-up, purchase, grant).

    Write operation - requires primary database.
    Requires: API key with billing:write permission.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=request.wa_id,
        tenant_id=request.tenant_id,
    )

    # Update account metadata on every credit request
    await service.update_account_metadata(
        identity=identity,
        customer_email=request.customer_email,
        display_name=request.display_name,
        marketing_opt_in=request.marketing_opt_in if request.marketing_opt_in else None,
        marketing_opt_in_source=request.marketing_opt_in_source,
        user_role=request.user_role,
        agent_id=request.agent_id,
    )

    intent = CreditIntent(
        account_identity=identity,
        amount_minor=request.amount_minor,
        currency=request.currency,
        description=request.description,
        transaction_type=request.transaction_type,
        external_transaction_id=request.external_transaction_id,
        idempotency_key=request.idempotency_key,
    )

    try:
        credit_data = await service.add_credits(intent)

        return CreditResponse(
            credit_id=credit_data.credit_id,
            account_id=credit_data.account_id,
            amount_minor=credit_data.amount_minor,
            currency=credit_data.currency,
            balance_after=credit_data.balance_after,
            transaction_type=credit_data.transaction_type,
            description=credit_data.description,
            external_transaction_id=credit_data.external_transaction_id,
            created_at=credit_data.created_at.isoformat(),
        )

    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc

    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Credit already exists",
            headers={"X-Existing-Credit-ID": str(exc.existing_id)},
        ) from exc

    except DataIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except WriteVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database integrity error",
        ) from exc


@router.post(
    "/v1/billing/purchases",
    response_model=PurchaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase(
    request: PurchaseRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> PurchaseResponse:
    """
    Create a payment intent for purchasing additional uses.

    This initiates a Stripe payment and returns the client secret for frontend.
    Write operation - requires primary database.
    Requires: API key with billing:write permission.
    """
    from app.config import settings
    from app.exceptions import PaymentProviderError
    from app.services.payment_provider import PaymentIntent
    from app.services.provider_config import ProviderConfigService
    from app.services.stripe_provider import StripeProvider

    service = BillingService(db)

    # Load Stripe config from database
    config_service = ProviderConfigService(db)
    stripe_config = await config_service.get_stripe_config()

    if not stripe_config or not stripe_config["api_key"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment provider not configured",
        )

    stripe_provider = StripeProvider(
        api_key=stripe_config["api_key"],
        webhook_secret=stripe_config["webhook_secret"],
    )

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=request.wa_id,
        tenant_id=request.tenant_id,
    )

    # Get or create account
    try:
        account_data = await service.get_or_create_account(
            identity=identity,
            initial_balance_minor=0,
            currency="USD",
            plan_name="free",
            customer_email=request.customer_email,
            display_name=request.display_name,
            marketing_opt_in=request.marketing_opt_in,
            marketing_opt_in_source=request.marketing_opt_in_source,
            user_role=request.user_role,
            agent_id=request.agent_id,
        )
    except WriteVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account",
        ) from exc

    # Create payment intent with Stripe
    # Use current timestamp for idempotency to allow multiple purchase attempts
    from datetime import datetime

    current_timestamp = int(datetime.now(UTC).timestamp())

    payment_intent = PaymentIntent(
        amount_minor=settings.price_per_purchase_minor,
        currency="USD",
        description=f"Purchase {settings.paid_uses_per_purchase} uses",
        customer_email=request.customer_email,
        metadata_account_id=str(account_data.account_id),
        metadata_oauth_provider=request.oauth_provider,
        metadata_external_id=request.external_id,
        idempotency_key=f"purchase-v3-{account_data.account_id}-{current_timestamp}",
    )

    try:
        payment_result = await stripe_provider.create_payment_intent(payment_intent)

        return PurchaseResponse(
            payment_id=payment_result.payment_id,
            client_secret=payment_result.client_secret,
            amount_minor=payment_result.amount_minor,
            currency=payment_result.currency,
            uses_purchased=settings.paid_uses_per_purchase,
            status=payment_result.status,
            publishable_key=stripe_config["publishable_key"],
        )

    except PaymentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment provider unavailable",
        ) from exc


@router.get(
    "/v1/billing/purchases/{payment_id}",
    response_model=PurchaseResponse,
)
async def get_purchase_status(
    payment_id: str,
    db: AsyncSession = Depends(get_read_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> PurchaseResponse:
    """
    Get status of a payment intent.

    This allows polling the payment status after initiating a purchase.
    Read operation - no database write needed.
    Requires: API key with billing:read permission.
    """
    from app.config import settings
    from app.exceptions import PaymentProviderError
    from app.services.provider_config import ProviderConfigService
    from app.services.stripe_provider import StripeProvider

    # Load Stripe config from database
    config_service = ProviderConfigService(db)
    stripe_config = await config_service.get_stripe_config()

    if not stripe_config or not stripe_config["api_key"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment provider not configured",
        )

    stripe_provider = StripeProvider(
        api_key=stripe_config["api_key"],
        webhook_secret=stripe_config["webhook_secret"],
    )

    try:
        payment_result = await stripe_provider.get_payment_status(payment_id)

        return PurchaseResponse(
            payment_id=payment_result.payment_id,
            client_secret=payment_result.client_secret,
            amount_minor=payment_result.amount_minor,
            currency=payment_result.currency,
            uses_purchased=settings.paid_uses_per_purchase,
            status=payment_result.status,
            publishable_key=stripe_config["publishable_key"],
        )

    except PaymentProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment not found: {payment_id}",
        ) from exc


@router.get(
    "/v1/billing/purchases/{payment_id}/status",
    response_model=PurchaseResponse,
)
async def get_purchase_status_alias(
    payment_id: str,
    db: AsyncSession = Depends(get_read_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> PurchaseResponse:
    """
    Get status of a payment intent (alias endpoint for agent compatibility).

    This is an alias for GET /v1/billing/purchases/{payment_id} to support
    the agent's expected endpoint pattern.
    Read operation - no database write needed.
    Requires: API key with billing:read permission.
    """
    # Delegate to the main endpoint
    return await get_purchase_status(payment_id, db, api_key)


@router.post(
    "/v1/billing/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_or_update_account(
    request: CreateAccountRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> AccountResponse:
    """
    Create new account or get existing one (upsert).

    Write operation - requires primary database.
    Requires: API key with billing:write permission.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=request.wa_id,
        tenant_id=request.tenant_id,
    )

    try:
        account_data = await service.get_or_create_account(
            identity=identity,
            initial_balance_minor=request.initial_balance_minor,
            currency=request.currency,
            plan_name=request.plan_name,
            customer_email=request.customer_email,
            display_name=request.display_name,
            marketing_opt_in=request.marketing_opt_in,
            marketing_opt_in_source=request.marketing_opt_in_source,
            user_role=request.user_role,
            agent_id=request.agent_id,
        )

        return AccountResponse(
            account_id=account_data.account_id,
            oauth_provider=account_data.oauth_provider,
            external_id=account_data.external_id,
            wa_id=account_data.wa_id,
            tenant_id=account_data.tenant_id,
            customer_email=account_data.customer_email,
            balance_minor=account_data.balance_minor,
            currency=account_data.currency,
            plan_name=account_data.plan_name,
            status=account_data.status,
            paid_credits=account_data.paid_credits,
            marketing_opt_in=account_data.marketing_opt_in,
            marketing_opt_in_at=account_data.marketing_opt_in_at.isoformat()
            if account_data.marketing_opt_in_at
            else None,
            marketing_opt_in_source=account_data.marketing_opt_in_source,
            created_at=account_data.created_at.isoformat(),
            updated_at=account_data.updated_at.isoformat(),
        )

    except WriteVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database integrity error",
        ) from exc


@router.get(
    "/v1/billing/accounts/{oauth_provider}/{external_id}",
    response_model=AccountResponse,
)
async def get_account(
    oauth_provider: str,
    external_id: str,
    wa_id: str | None = None,
    tenant_id: str | None = None,
    db: AsyncSession = Depends(get_read_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> AccountResponse:
    """
    Get account by identity.

    Read operation - served from replica.
    Requires: API key with billing:read permission.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=oauth_provider,
        external_id=external_id,
        wa_id=wa_id,
        tenant_id=tenant_id,
    )

    try:
        account_data = await service.get_account(identity)

        return AccountResponse(
            account_id=account_data.account_id,
            oauth_provider=account_data.oauth_provider,
            external_id=account_data.external_id,
            wa_id=account_data.wa_id,
            tenant_id=account_data.tenant_id,
            customer_email=account_data.customer_email,
            balance_minor=account_data.balance_minor,
            currency=account_data.currency,
            plan_name=account_data.plan_name,
            status=account_data.status,
            paid_credits=account_data.paid_credits,
            marketing_opt_in=account_data.marketing_opt_in,
            marketing_opt_in_at=account_data.marketing_opt_in_at.isoformat()
            if account_data.marketing_opt_in_at
            else None,
            marketing_opt_in_source=account_data.marketing_opt_in_source,
            created_at=account_data.created_at.isoformat(),
            updated_at=account_data.updated_at.isoformat(),
        )

    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        ) from exc


@router.post("/v1/billing/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_write_db),
) -> dict[str, str]:
    """
    Handle Stripe webhook events.

    Processes payment confirmations and updates account balances.
    """
    from structlog import get_logger

    from app.config import settings
    from app.exceptions import WebhookVerificationError
    from app.services.provider_config import ProviderConfigService
    from app.services.stripe_provider import StripeProvider

    logger = get_logger(__name__)

    # Load Stripe config from database
    config_service = ProviderConfigService(db)
    stripe_config = await config_service.get_stripe_config()

    if not stripe_config or not stripe_config["api_key"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment provider not configured",
        )

    stripe_provider = StripeProvider(
        api_key=stripe_config["api_key"],
        webhook_secret=stripe_config["webhook_secret"],
    )

    # Read raw webhook payload
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        # Verify webhook signature and parse event
        webhook_event = await stripe_provider.verify_webhook(payload, signature)

        logger.info(
            "stripe_webhook_received",
            event_id=webhook_event.event_id,
            event_type=webhook_event.event_type,
            payment_id=webhook_event.payment_id,
        )

        # Handle payment success
        if webhook_event.event_type == "payment_intent.succeeded":
            # Extract account info from metadata
            if (
                not webhook_event.metadata_account_id
                or not webhook_event.metadata_oauth_provider
                or not webhook_event.metadata_external_id
            ):
                logger.error("stripe_webhook_missing_metadata", event_id=webhook_event.event_id)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing account metadata in webhook",
                )

            # Confirm payment with Stripe
            payment_succeeded = await stripe_provider.confirm_payment(webhook_event.payment_id)

            if payment_succeeded:
                # Reconstruct account identity from metadata
                identity = AccountIdentity(
                    oauth_provider=webhook_event.metadata_oauth_provider,
                    external_id=webhook_event.metadata_external_id,
                    wa_id=None,
                    tenant_id=None,
                )

                # Add purchased uses to account
                service = BillingService(db)
                try:
                    await service.add_purchased_uses(
                        identity=identity,
                        uses_to_add=settings.paid_uses_per_purchase,
                        payment_id=webhook_event.payment_id,
                        amount_paid_minor=webhook_event.amount_minor
                        or settings.price_per_purchase_minor,
                    )
                    logger.info(
                        "stripe_payment_credited",
                        payment_id=webhook_event.payment_id,
                        account_id=webhook_event.metadata_account_id,
                        uses_added=settings.paid_uses_per_purchase,
                    )
                except Exception as e:
                    logger.error(
                        "stripe_webhook_credit_failed",
                        payment_id=webhook_event.payment_id,
                        error=str(e),
                    )
                    # Don't raise - we confirmed the payment succeeded
                    # The user will need manual credit adjustment

            return {"status": "success", "event_id": webhook_event.event_id}

        # Handle payment failure
        elif webhook_event.event_type == "payment_intent.payment_failed":
            logger.warning(
                "stripe_payment_failed",
                event_id=webhook_event.event_id,
                payment_id=webhook_event.payment_id,
            )
            return {"status": "acknowledged", "event_id": webhook_event.event_id}

        # Acknowledge other events
        else:
            logger.info(
                "stripe_webhook_ignored",
                event_type=webhook_event.event_type,
                event_id=webhook_event.event_id,
            )
            return {"status": "ignored", "event_id": webhook_event.event_id}

    except WebhookVerificationError as exc:
        logger.error("stripe_webhook_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        ) from exc

    except Exception as exc:
        logger.error("stripe_webhook_processing_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed",
        ) from exc


# =============================================================================
# Google Play Endpoints
# =============================================================================


@router.post(
    "/v1/billing/google-play/verify",
    response_model=GooglePlayVerifyResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_google_play_purchase(
    request: GooglePlayVerifyRequest,
    db: AsyncSession = Depends(get_write_db),
    auth: CombinedAuth = Depends(require_permission_or_jwt("billing:write")),
) -> GooglePlayVerifyResponse:
    """
    Verify Google Play purchase and add credits to account.

    Flow:
    1. Android app completes purchase via Google Play Billing Library
    2. App receives purchase token
    3. App calls this endpoint with token
    4. Backend verifies with Google Play Developer API
    5. Backend consumes purchase (prevents reuse)
    6. Backend credits user's account

    Idempotent: Same purchase_token can be called multiple times safely.
    Only the first call will add credits.

    Auth: API key with billing:write permission OR Bearer {google_id_token}
    """
    from sqlalchemy import select
    from structlog import get_logger

    from app.db.models import GooglePlayPurchase
    from app.exceptions import PaymentProviderError
    from app.models.google_play import GooglePlayPurchaseToken
    from app.services.google_play_products import get_credits_for_product
    from app.services.google_play_provider import GooglePlayProvider
    from app.services.provider_config import ProviderConfigService

    logger = get_logger(__name__)

    service = BillingService(db)

    # If JWT auth, use identity from token; otherwise use request body
    if auth.auth_type == "jwt" and auth.user:
        identity = AccountIdentity(
            oauth_provider=auth.user.oauth_provider,
            external_id=auth.user.external_id,
            wa_id=request.wa_id if request else None,
            tenant_id=request.tenant_id if request else None,
        )
    else:
        identity = AccountIdentity(
            oauth_provider=request.oauth_provider,
            external_id=request.external_id,
            wa_id=request.wa_id,
            tenant_id=request.tenant_id,
        )

    # Check if purchase already processed (idempotency)
    existing_stmt = select(GooglePlayPurchase).where(
        GooglePlayPurchase.purchase_token == request.purchase_token
    )
    existing_result = await db.execute(existing_stmt)
    existing_purchase = existing_result.scalar_one_or_none()

    if existing_purchase:
        logger.info(
            "google_play_purchase_already_processed",
            order_id=existing_purchase.order_id,
            credits_added=existing_purchase.credits_added,
        )
        # Get current account balance
        try:
            account_data = await service.get_account(identity)
            balance_after = account_data.paid_credits
        except AccountNotFoundError:
            balance_after = existing_purchase.credits_added

        return GooglePlayVerifyResponse(
            success=True,
            credits_added=existing_purchase.credits_added,
            new_balance=balance_after,
            order_id=existing_purchase.order_id,
            purchase_time_millis=existing_purchase.purchase_time_millis,
            already_processed=True,
        )

    # Load Google Play config from database
    config_service = ProviderConfigService(db)
    google_play_config = await config_service.get_google_play_config()

    if not google_play_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Play provider not configured",
        )

    try:
        # Initialize provider
        provider = GooglePlayProvider(
            service_account_json=google_play_config["service_account_json"],
            package_name=google_play_config["package_name"],
        )

        # Verify purchase with Google Play
        purchase_token = GooglePlayPurchaseToken(
            token=request.purchase_token,
            product_id=request.product_id,
            package_name=request.package_name,
        )
        verification = await provider.verify_purchase(purchase_token)

        # Validate purchase state
        if not verification.is_valid():
            logger.warning(
                "google_play_purchase_invalid",
                order_id=verification.order_id,
                purchase_state=verification.purchase_state,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Purchase not valid: state={verification.purchase_state}",
            )

        # Get credits for product
        try:
            credits_to_add = get_credits_for_product(request.product_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Get or create account
        account_data = await service.get_or_create_account(
            identity=identity,
            initial_balance_minor=0,
            currency="USD",
            plan_name="free",
            customer_email=request.customer_email,
            display_name=request.display_name,
            marketing_opt_in=request.marketing_opt_in,
            marketing_opt_in_source=request.marketing_opt_in_source,
            user_role=request.user_role,
            agent_id=request.agent_id,
        )

        # Add credits
        await service.add_purchased_uses(
            identity=identity,
            uses_to_add=credits_to_add,
            payment_id=verification.order_id,
            amount_paid_minor=0,  # Google Play handles payment
            is_test=verification.is_test_purchase(),
        )

        # Get updated account
        account_data = await service.get_account(identity)

        # Record purchase for idempotency
        purchase_record = GooglePlayPurchase(
            account_id=account_data.account_id,
            purchase_token=request.purchase_token,
            order_id=verification.order_id,
            product_id=request.product_id,
            package_name=request.package_name,
            purchase_time_millis=verification.purchase_time_millis,
            purchase_state=verification.purchase_state,
            acknowledged=False,
            consumed=False,
            credits_added=credits_to_add,
        )
        db.add(purchase_record)

        # Consume purchase (prevents reuse)
        try:
            await provider.consume_purchase(
                purchase_token=request.purchase_token,
                product_id=request.product_id,
            )
            purchase_record.consumed = True
            purchase_record.acknowledged = True
        except PaymentProviderError as exc:
            logger.error(
                "google_play_consume_failed",
                order_id=verification.order_id,
                error=str(exc),
            )
            # Don't fail - credits are already added

        await db.commit()

        logger.info(
            "google_play_purchase_verified",
            order_id=verification.order_id,
            product_id=request.product_id,
            credits_added=credits_to_add,
            account_id=str(account_data.account_id),
            is_test=verification.is_test_purchase(),
        )

        return GooglePlayVerifyResponse(
            success=True,
            credits_added=credits_to_add,
            new_balance=account_data.paid_credits,
            order_id=verification.order_id,
            purchase_time_millis=verification.purchase_time_millis,
            already_processed=False,
        )

    except PaymentProviderError as exc:
        logger.error("google_play_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("google_play_verification_unexpected_error")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification failed",
        ) from exc


@router.post("/v1/billing/webhooks/google-play")
async def google_play_webhook(
    request: Request,
    db: AsyncSession = Depends(get_write_db),
) -> dict[str, str]:
    """
    Handle Google Play Real-Time Developer Notifications (RTDN).

    Google sends notifications for:
    - ONE_TIME_PRODUCT_PURCHASED (type=1)
    - ONE_TIME_PRODUCT_CANCELED (type=2)

    This is supplementary to the verify endpoint. The verify endpoint
    is the primary flow. Webhooks handle edge cases like refunds.
    """
    from structlog import get_logger

    from app.exceptions import WebhookVerificationError
    from app.services.google_play_provider import GooglePlayProvider
    from app.services.provider_config import ProviderConfigService

    logger = get_logger(__name__)

    # Read raw webhook payload
    payload = await request.body()

    # Load Google Play config
    config_service = ProviderConfigService(db)
    google_play_config = await config_service.get_google_play_config()

    if not google_play_config:
        logger.warning("google_play_webhook_provider_not_configured")
        return {"status": "ignored", "reason": "provider_not_configured"}

    try:
        # Initialize provider and verify webhook
        provider = GooglePlayProvider(
            service_account_json=google_play_config["service_account_json"],
            package_name=google_play_config["package_name"],
        )
        webhook_event = await provider.verify_webhook(payload)

        logger.info(
            "google_play_webhook_received",
            event_type=webhook_event.event_type,
            product_id=webhook_event.product_id,
            notification_type=webhook_event.notification_type,
        )

        # Handle cancellation/refund (type=2)
        if webhook_event.notification_type == 2:
            logger.warning(
                "google_play_purchase_canceled",
                product_id=webhook_event.product_id,
                purchase_token=webhook_event.purchase_token[:20] + "...",
            )
            # TODO: Implement credit clawback for refunds
            # For now, log for manual review

        return {"status": "ok", "event_type": webhook_event.event_type}

    except WebhookVerificationError as exc:
        logger.error("google_play_webhook_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook payload",
        ) from exc

    except Exception as exc:
        logger.exception("google_play_webhook_processing_failed")
        # Return 200 to prevent retries - we'll investigate manually
        return {"status": "error", "message": str(exc)}


@router.get("/v1/billing/transactions", response_model=TransactionListResponse)
async def list_transactions(
    oauth_provider: str,
    external_id: str,
    wa_id: str | None = None,
    tenant_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_read_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> TransactionListResponse:
    """
    List all transactions (charges and credits) for an account.

    Returns a unified list of charges (debits) and credits sorted by timestamp.
    Read operation - served from replica.
    Requires: API key with billing:read permission.
    """
    from sqlalchemy import select

    from app.db.models import Charge, Credit

    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=oauth_provider,
        external_id=external_id,
        wa_id=wa_id,
        tenant_id=tenant_id,
    )

    # Get account
    try:
        account_data = await service.get_account(identity)
    except AccountNotFoundError:
        # Account doesn't exist - return empty list
        return TransactionListResponse(
            transactions=[],
            total_count=0,
            has_more=False,
        )

    # Get charges (debits)
    charges_stmt = select(
        Charge.id.label("transaction_id"),
        Charge.amount_minor,
        Charge.currency,
        Charge.description,
        Charge.created_at,
        Charge.balance_after,
        Charge.metadata_message_id,
        Charge.metadata_agent_id,
        Charge.metadata_channel_id,
        Charge.metadata_request_id,
    ).where(Charge.account_id == account_data.account_id)

    charges_result = await db.execute(charges_stmt)
    charges = charges_result.all()

    # Get credits
    credits_stmt = select(
        Credit.id.label("transaction_id"),
        Credit.amount_minor,
        Credit.currency,
        Credit.description,
        Credit.created_at,
        Credit.balance_after,
        Credit.transaction_type,
        Credit.external_transaction_id,
    ).where(Credit.account_id == account_data.account_id)

    credits_result = await db.execute(credits_stmt)
    credits = credits_result.all()

    # Combine into transaction list
    transactions = []

    # Add charges (as negative amounts)
    for charge in charges:
        transactions.append(
            TransactionItem(
                transaction_id=charge.transaction_id,
                type="charge",
                amount_minor=-charge.amount_minor,  # Negative for debits
                currency=charge.currency,
                description=charge.description,
                created_at=charge.created_at.isoformat(),
                balance_after=charge.balance_after,
                metadata=ChargeMetadata(
                    message_id=charge.metadata_message_id,
                    agent_id=charge.metadata_agent_id,
                    channel_id=charge.metadata_channel_id,
                    request_id=charge.metadata_request_id,
                ),
            )
        )

    # Add credits (as positive amounts)
    for credit in credits:
        transactions.append(
            TransactionItem(
                transaction_id=credit.transaction_id,
                type="credit",
                amount_minor=credit.amount_minor,  # Positive for credits
                currency=credit.currency,
                description=credit.description,
                created_at=credit.created_at.isoformat(),
                balance_after=credit.balance_after,
                transaction_type=credit.transaction_type,
                external_transaction_id=credit.external_transaction_id,
            )
        )

    # Sort by created_at DESC
    transactions.sort(key=lambda t: t.created_at, reverse=True)

    # Apply pagination
    total_count = len(transactions)
    paginated_transactions = transactions[offset : offset + limit]
    has_more = (offset + limit) < total_count

    return TransactionListResponse(
        transactions=paginated_transactions,
        total_count=total_count,
        has_more=has_more,
    )


# ============================================================================
# LiteLLM Proxy Integration Endpoints
# ============================================================================
# NOTE: /v1/billing/litellm/auth and /v1/billing/litellm/charge were removed
# as redundant. Use these endpoints instead:
#   - Auth check: POST /v1/billing/credits/check (supports API key or JWT)
#   - Charge: POST /v1/billing/charges (API key required)
# The /v1/billing/litellm/usage endpoint remains for LLM cost analytics.


@router.post(
    "/v1/billing/litellm/usage/debug",
    status_code=status.HTTP_200_OK,
)
async def litellm_log_usage_debug(
    request: Request,
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> dict[str, Any]:
    """Debug endpoint to capture raw request body."""
    from structlog import get_logger

    logger = get_logger(__name__)
    body = await request.body()
    try:
        import json

        parsed = json.loads(body)
        logger.info("usage_debug_received", body=parsed)
        return {"received": parsed, "body_length": len(body)}
    except Exception as e:
        logger.error("usage_debug_parse_error", error=str(e), raw_body=body.decode()[:500])
        return {"error": str(e), "raw_body": body.decode()[:500]}


@router.post(
    "/v1/billing/litellm/usage",
    response_model=LiteLLMUsageLogResponse,
    status_code=status.HTTP_201_CREATED,
)
async def litellm_log_usage(
    request: LiteLLMUsageLogRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> LiteLLMUsageLogResponse:
    """
    Log LLM usage for analytics.

    Called by LiteLLM proxy to record actual provider costs.
    This is for YOUR margin analytics - users pay 1 credit regardless.

    Tracks:
    - Total LLM calls per interaction (pondering loops = 12-70 calls)
    - Token usage (prompt + completion)
    - Models used (Groq, Together, etc.)
    - Actual cost in cents
    - Duration and error counts
    """
    from uuid import uuid4

    from sqlalchemy import select

    from app.db.models import Account, LLMUsageLog

    # Find account
    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=None,
        tenant_id=None,
    )

    stmt = select(Account).where(
        Account.oauth_provider == identity.oauth_provider,
        Account.external_id == identity.external_id,
    )
    result = await db.execute(stmt)
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    # Create usage log
    usage_log = LLMUsageLog(
        id=uuid4(),
        account_id=account.id,
        interaction_id=request.interaction_id,
        charge_id=None,  # Could be linked later if needed
        total_llm_calls=request.total_llm_calls,
        total_prompt_tokens=request.total_prompt_tokens,
        total_completion_tokens=request.total_completion_tokens,
        models_used=request.models_used,
        actual_cost_cents=request.actual_cost_cents,
        duration_ms=request.duration_ms,
        error_count=request.error_count,
        fallback_count=request.fallback_count,
    )

    db.add(usage_log)
    await db.commit()

    return LiteLLMUsageLogResponse(
        logged=True,
        usage_log_id=usage_log.id,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_read_db)) -> HealthResponse:
    """
    Health check for load balancer.

    Verifies database connectivity.
    """
    from datetime import datetime

    try:
        # Test database connection
        await db.execute(text("SELECT 1"))

        return HealthResponse(
            status="healthy",
            database="connected",
            timestamp=datetime.now(UTC).isoformat(),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "database": "disconnected",
                "error": str(exc),
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ) from exc


# =============================================================================
# User-Facing Endpoints (Google ID Token auth - no API key required)
# =============================================================================
# These endpoints allow Android/mobile clients to authenticate using their
# Google ID token directly, without embedding an API key in the APK.
# The Google ID token is cryptographically signed by Google and verified
# against Google's public keys.
#
# NOTE: GET /v1/user/balance was removed as redundant.
# Use POST /v1/billing/credits/check with Bearer {google_id_token} instead.
# It accepts JWT auth and returns the same balance information.


@router.post(
    "/v1/user/google-play/verify",
    response_model=UserGooglePlayVerifyResponse,
    status_code=status.HTTP_200_OK,
)
async def user_verify_google_play_purchase(
    request: UserGooglePlayVerifyRequest,
    user: UserIdentity = Depends(get_user_from_google_token),
    db: AsyncSession = Depends(get_write_db),
) -> UserGooglePlayVerifyResponse:
    """
    Verify a Google Play purchase and add credits to the authenticated user's account.

    Auth: Bearer {google_id_token}

    Flow:
    1. Android app completes purchase via Google Play Billing Library
    2. App receives purchase token
    3. App calls this endpoint with token + Google ID token auth
    4. Backend verifies purchase with Google Play Developer API
    5. Backend consumes purchase (prevents reuse)
    6. Backend credits user's account

    Idempotent: Same purchase_token can be called multiple times safely.
    """
    from sqlalchemy import select
    from structlog import get_logger

    from app.db.models import GooglePlayPurchase
    from app.exceptions import PaymentProviderError
    from app.models.google_play import GooglePlayPurchaseToken
    from app.services.google_play_products import get_credits_for_product
    from app.services.google_play_provider import GooglePlayProvider
    from app.services.provider_config import ProviderConfigService

    logger = get_logger(__name__)

    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=user.oauth_provider,
        external_id=user.external_id,
        wa_id=None,
        tenant_id=None,
    )

    # Check if purchase already processed (idempotency)
    existing_stmt = select(GooglePlayPurchase).where(
        GooglePlayPurchase.purchase_token == request.purchase_token
    )
    existing_result = await db.execute(existing_stmt)
    existing_purchase = existing_result.scalar_one_or_none()

    if existing_purchase:
        logger.info(
            "google_play_purchase_already_processed",
            order_id=existing_purchase.order_id,
            credits_added=existing_purchase.credits_added,
        )
        # Get current account balance
        try:
            account_data = await service.get_account(identity)
            new_balance = account_data.paid_credits + account_data.free_uses_remaining
        except AccountNotFoundError:
            new_balance = existing_purchase.credits_added

        return UserGooglePlayVerifyResponse(
            success=True,
            credits_added=existing_purchase.credits_added,
            new_balance=new_balance,
            already_processed=True,
        )

    # Load Google Play config from database
    config_service = ProviderConfigService(db)
    google_play_config = await config_service.get_google_play_config()

    if not google_play_config:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Play provider not configured",
        )

    try:
        # Initialize provider
        provider = GooglePlayProvider(
            service_account_json=google_play_config["service_account_json"],
            package_name=google_play_config["package_name"],
        )

        # Verify purchase with Google Play
        purchase_token = GooglePlayPurchaseToken(
            token=request.purchase_token,
            product_id=request.product_id,
            package_name=request.package_name,
        )
        verification = await provider.verify_purchase(purchase_token)

        # Validate purchase state
        if not verification.is_valid():
            logger.warning(
                "google_play_purchase_invalid",
                order_id=verification.order_id,
                purchase_state=verification.purchase_state,
            )
            return UserGooglePlayVerifyResponse(
                success=False,
                credits_added=0,
                new_balance=0,
                already_processed=False,
            )

        # Get credits for product
        try:
            credits_to_add = get_credits_for_product(request.product_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        # Get or create account
        account_data = await service.get_or_create_account(
            identity=identity,
            initial_balance_minor=0,
            currency="USD",
            plan_name="free",
            customer_email=user.email,
            display_name=user.name,
        )

        # Add credits
        await service.add_purchased_uses(
            identity=identity,
            uses_to_add=credits_to_add,
            payment_id=verification.order_id,
            amount_paid_minor=0,  # Google Play handles payment
            is_test=verification.is_test_purchase(),
        )

        # Get updated account
        account_data = await service.get_account(identity)
        new_balance = account_data.paid_credits + account_data.free_uses_remaining

        # Record purchase for idempotency
        purchase_record = GooglePlayPurchase(
            account_id=account_data.account_id,
            purchase_token=request.purchase_token,
            order_id=verification.order_id,
            product_id=request.product_id,
            package_name=request.package_name,
            purchase_time_millis=verification.purchase_time_millis,
            purchase_state=verification.purchase_state,
            acknowledged=False,
            consumed=False,
            credits_added=credits_to_add,
        )
        db.add(purchase_record)

        # Consume purchase (prevents reuse)
        try:
            await provider.consume_purchase(
                purchase_token=request.purchase_token,
                product_id=request.product_id,
            )
            purchase_record.consumed = True
            purchase_record.acknowledged = True
        except PaymentProviderError as exc:
            logger.error(
                "google_play_consume_failed",
                order_id=verification.order_id,
                error=str(exc),
            )
            # Don't fail - credits are already added

        await db.commit()

        logger.info(
            "google_play_purchase_verified",
            order_id=verification.order_id,
            product_id=request.product_id,
            credits_added=credits_to_add,
            account_id=str(account_data.account_id),
            is_test=verification.is_test_purchase(),
        )

        return UserGooglePlayVerifyResponse(
            success=True,
            credits_added=credits_to_add,
            new_balance=new_balance,
            already_processed=False,
        )

    except PaymentProviderError as exc:
        logger.error("google_play_verification_failed", error=str(exc))
        return UserGooglePlayVerifyResponse(
            success=False,
            credits_added=0,
            new_balance=0,
            already_processed=False,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("google_play_verification_unexpected_error")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        ) from exc


# =============================================================================
# Play Integrity API Endpoints
# =============================================================================
# These endpoints provide Play Integrity verification for high-security operations.
# Use this to verify the Android app is genuine and running on a trusted device.
#
# Flow:
# 1. App calls GET /v1/integrity/nonce to get a nonce
# 2. App requests integrity token from Google with the nonce
# 3. App calls POST /v1/integrity/verify with the token
# 4. For combined auth+integrity, use POST /v1/integrity/auth


@router.get(
    "/v1/integrity/nonce",
    status_code=status.HTTP_200_OK,
)
async def get_integrity_nonce(
    context: str | None = None,
) -> dict[str, str]:
    """
    Get a nonce for Play Integrity verification.

    The nonce is:
    - Cryptographically secure
    - Single-use (consumed on verification)
    - Short-lived (expires in 5 minutes)

    Args:
        context: Optional context (e.g., "purchase", "login", "credit_check")

    Returns:
        nonce: Base64 URL-safe encoded nonce
        expires_at: When this nonce expires
    """
    from app.config import settings
    from app.services.play_integrity import PlayIntegrityConfig, PlayIntegrityService

    # Initialize service (doesn't require service account for nonce generation)
    config = PlayIntegrityConfig(
        package_name=settings.ANDROID_PACKAGE_NAME or "ai.ciris.agent",
    )
    service = PlayIntegrityService(config)

    nonce, expires_at = service.generate_nonce(context=context)

    return {
        "nonce": nonce,
        "expires_at": expires_at.isoformat(),
    }


@router.post(
    "/v1/integrity/verify",
    status_code=status.HTTP_200_OK,
)
async def verify_integrity(
    integrity_token: str,
    nonce: str,
) -> dict[str, Any]:
    """
    Verify a Play Integrity token.

    This endpoint decodes the integrity token using Google's API
    and returns the device/app/account verdicts.

    Args:
        integrity_token: The encrypted token from Android
        nonce: The nonce that was used to request this token

    Returns:
        verified: Whether integrity check passed
        device_integrity: Device verdicts (MEETS_BASIC_INTEGRITY, etc.)
        app_integrity: App verdict (PLAY_RECOGNIZED, etc.)
        account_details: Licensing verdict
    """
    from app.config import settings
    from app.services.play_integrity import PlayIntegrityConfig, PlayIntegrityService

    if not settings.PLAY_INTEGRITY_SERVICE_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Play Integrity API not configured",
        )

    config = PlayIntegrityConfig(
        package_name=settings.ANDROID_PACKAGE_NAME or "ai.ciris.agent",
        service_account_json=settings.PLAY_INTEGRITY_SERVICE_ACCOUNT,
    )
    service = PlayIntegrityService(config)

    result = await service.verify_token(integrity_token, nonce)

    return result.model_dump()


@router.post(
    "/v1/integrity/auth",
    status_code=status.HTTP_200_OK,
)
async def verify_integrity_with_auth(
    integrity_token: str,
    nonce: str,
    user: UserIdentity = Depends(get_user_from_google_token),
) -> dict[str, Any]:
    """
    Combined JWT + Play Integrity verification.

    Use this for high-security operations that require both:
    - User authentication (JWT from Google Sign-In)
    - Device/app integrity (Play Integrity API)

    Recommended for:
    - First app launch / registration
    - Before processing payments
    - Granting premium features
    - Periodically (once per session)

    Auth: Bearer {google_id_token}

    Args:
        integrity_token: The encrypted Play Integrity token
        nonce: The nonce used to request the token

    Returns:
        authenticated: JWT auth passed
        integrity_verified: Play Integrity passed
        user_id: Google user ID from JWT
        authorized: Both checks passed
    """
    from structlog import get_logger

    from app.config import settings
    from app.services.play_integrity import PlayIntegrityConfig, PlayIntegrityService

    logger = get_logger(__name__)

    # JWT auth already verified by dependency
    authenticated = True
    user_id = user.external_id
    email = user.email

    # Check if Play Integrity is configured
    if not settings.PLAY_INTEGRITY_SERVICE_ACCOUNT:
        logger.warning("play_integrity_not_configured")
        return {
            "authenticated": authenticated,
            "integrity_verified": False,
            "user_id": user_id,
            "email": email,
            "authorized": False,
            "reason": "Play Integrity API not configured",
        }

    # Verify Play Integrity
    config = PlayIntegrityConfig(
        package_name=settings.ANDROID_PACKAGE_NAME or "ai.ciris.agent",
        service_account_json=settings.PLAY_INTEGRITY_SERVICE_ACCOUNT,
    )
    service = PlayIntegrityService(config)

    result = await service.verify_token(integrity_token, nonce)

    authorized = authenticated and result.verified

    logger.info(
        "integrity_auth_complete",
        user_id=user_id,
        authenticated=authenticated,
        integrity_verified=result.verified,
        authorized=authorized,
    )

    return {
        "authenticated": authenticated,
        "integrity_verified": result.verified,
        "user_id": user_id,
        "email": email,
        "device_integrity": result.device_integrity.model_dump()
        if result.device_integrity
        else None,
        "app_integrity": result.app_integrity.model_dump() if result.app_integrity else None,
        "authorized": authorized,
        "reason": result.error if not result.verified else None,
    }
