"""
API Routes - FastAPI endpoints for billing operations.

NO DICTIONARIES - All requests/responses use Pydantic models.
"""

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_permission
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
    LiteLLMAuthRequest,
    LiteLLMAuthResponse,
    LiteLLMChargeRequest,
    LiteLLMChargeResponse,
    LiteLLMUsageLogRequest,
    LiteLLMUsageLogResponse,
    PurchaseRequest,
    PurchaseResponse,
    TransactionItem,
    TransactionListResponse,
)
from app.models.domain import AccountIdentity, ChargeIntent, CreditIntent
from app.services.api_key import APIKeyData
from app.services.billing import BillingService

router = APIRouter()


@router.post("/v1/billing/credits/check", response_model=CreditCheckResponse)
async def check_credit(
    request: CreditCheckRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> CreditCheckResponse:
    """
    Check if account has sufficient credits.

    Auto-creates new accounts with free credits on first check.
    Updates account metadata if provided for existing accounts.
    Write operation - requires primary database.
    Requires: API key with billing:read permission.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=request.wa_id,
        tenant_id=request.tenant_id,
    )

    # First check credit (creates account if needed)
    result = await service.check_credit(
        identity,
        request.context,
        customer_email=request.customer_email,
        marketing_opt_in=request.marketing_opt_in,
        marketing_opt_in_source=request.marketing_opt_in_source,
        user_role=request.user_role,
        agent_id=request.agent_id,
    )

    # Update account metadata if provided (for existing accounts)
    # Always call update to ensure metadata is synced on every request
    await service.update_account_metadata(
        identity=identity,
        customer_email=request.customer_email,
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
    api_key: APIKeyData = Depends(require_permission("billing:write")),
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

    Requires: API key with billing:write permission.
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
            verified=True,
            credits_added=existing_purchase.credits_added,
            balance_after=balance_after,
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
        )

        return GooglePlayVerifyResponse(
            verified=True,
            credits_added=credits_to_add,
            balance_after=account_data.paid_credits,
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


@router.post("/v1/billing/litellm/auth", response_model=LiteLLMAuthResponse)
async def litellm_auth_check(
    request: LiteLLMAuthRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:read")),
) -> LiteLLMAuthResponse:
    """
    LiteLLM pre-request authorization check.

    Called by LiteLLM proxy before allowing an interaction.
    Checks if user has at least 1 credit available.

    Returns:
        authorized: True if user has >= 1 credit
        credits_remaining: Current balance (before potential charge)
        interaction_id: Echo back or generate for tracking
    """
    from uuid import uuid4

    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=None,
        tenant_id=None,
    )

    # Check credit (this also creates account if it doesn't exist)
    result = await service.check_credit(identity, context=None)

    # Generate interaction_id if not provided
    interaction_id = request.interaction_id or str(uuid4())

    # User needs at least 1 credit (100 minor units = 1 credit)
    has_credit = result.has_credit and (result.credits_remaining or 0) >= 100

    return LiteLLMAuthResponse(
        authorized=has_credit,
        credits_remaining=(result.credits_remaining or 0) // 100,  # Convert to credits
        reason=None if has_credit else "Insufficient credits",
        interaction_id=interaction_id,
    )


@router.post(
    "/v1/billing/litellm/charge",
    response_model=LiteLLMChargeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def litellm_charge(
    request: LiteLLMChargeRequest,
    db: AsyncSession = Depends(get_write_db),
    api_key: APIKeyData = Depends(require_permission("billing:write")),
) -> LiteLLMChargeResponse:
    """
    LiteLLM post-interaction charge.

    Called by LiteLLM proxy after a successful interaction completes.
    Deducts exactly 1 credit (100 minor units) per interaction.

    Uses idempotency_key (defaults to interaction_id) to prevent double-charging.
    """
    service = BillingService(db)

    identity = AccountIdentity(
        oauth_provider=request.oauth_provider,
        external_id=request.external_id,
        wa_id=None,
        tenant_id=None,
    )

    # Use interaction_id as default idempotency key
    idempotency_key = request.idempotency_key or f"litellm:{request.interaction_id}"

    intent = ChargeIntent(
        account_identity=identity,
        amount_minor=100,  # 1 credit = 100 minor units
        currency="USD",
        description=f"LiteLLM interaction: {request.interaction_id}",
        metadata=ChargeMetadata(request_id=request.interaction_id),
        idempotency_key=idempotency_key,
    )

    try:
        charge_data = await service.create_charge(intent)

        return LiteLLMChargeResponse(
            charged=True,
            credits_deducted=1,
            credits_remaining=charge_data.balance_after // 100,  # Convert to credits
            charge_id=charge_data.charge_id,
        )

    except AccountNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    except InsufficientCreditsError as exc:
        # Return success=False instead of error for idempotent handling
        return LiteLLMChargeResponse(
            charged=False,
            credits_deducted=0,
            credits_remaining=exc.balance // 100,
            charge_id=None,
        )

    except IdempotencyConflictError as exc:
        # Already charged - return success (idempotent)
        return LiteLLMChargeResponse(
            charged=True,
            credits_deducted=1,
            credits_remaining=0,  # Unknown, but charge was successful
            charge_id=exc.existing_id,
        )

    except (AccountSuspendedError, AccountClosedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


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
