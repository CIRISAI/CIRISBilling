"""
Stripe Payment Provider Implementation.

NO DICTIONARIES - All data uses strongly typed models.
"""

import stripe
from structlog import get_logger

from app.exceptions import PaymentProviderError, WebhookVerificationError
from app.services.payment_provider import (
    PaymentIntent,
    PaymentResult,
    WebhookEvent,
)

logger = get_logger(__name__)


class StripeProvider:
    """
    Stripe payment provider implementation.

    Implements the PaymentProvider protocol for Stripe.
    """

    def __init__(self, api_key: str, webhook_secret: str) -> None:
        """
        Initialize Stripe provider.

        Args:
            api_key: Stripe secret API key
            webhook_secret: Stripe webhook signing secret
        """
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        stripe.api_key = api_key

    async def create_payment_intent(self, intent: PaymentIntent) -> PaymentResult:
        """
        Create a Stripe PaymentIntent.

        Args:
            intent: Payment intent details

        Returns:
            Payment result with Stripe payment intent ID and client secret

        Raises:
            PaymentProviderError: If Stripe API call fails
        """
        try:
            logger.info(
                "creating_stripe_payment_intent",
                amount_minor=intent.amount_minor,
                currency=intent.currency,
                idempotency_key=intent.idempotency_key,
            )

            # Create Stripe PaymentIntent
            payment_intent = stripe.PaymentIntent.create(
                amount=intent.amount_minor,
                currency=intent.currency.lower(),
                description=intent.description,
                receipt_email=intent.customer_email,
                metadata={
                    "account_id": intent.metadata_account_id,
                    "oauth_provider": intent.metadata_oauth_provider,
                    "external_id": intent.metadata_external_id,
                },
                idempotency_key=intent.idempotency_key,
            )

            logger.info(
                "stripe_payment_intent_created",
                payment_intent_id=payment_intent.id,
                status=payment_intent.status,
            )

            return PaymentResult(
                payment_id=payment_intent.id,
                client_secret=payment_intent.client_secret or "",
                status=payment_intent.status,
                amount_minor=payment_intent.amount,
                currency=payment_intent.currency.upper(),
            )

        except stripe.StripeError as exc:
            logger.error(
                "stripe_payment_intent_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise PaymentProviderError(f"Stripe payment failed: {exc}") from exc

    async def get_payment_status(self, payment_id: str) -> PaymentResult:
        """
        Get current status of a payment intent from Stripe.

        Args:
            payment_id: Stripe PaymentIntent ID

        Returns:
            PaymentResult with current status

        Raises:
            PaymentProviderError: If Stripe API call fails
        """
        try:
            logger.info("getting_stripe_payment_status", payment_intent_id=payment_id)

            payment_intent = stripe.PaymentIntent.retrieve(payment_id)

            logger.info(
                "stripe_payment_status_retrieved",
                payment_intent_id=payment_id,
                status=payment_intent.status,
            )

            return PaymentResult(
                payment_id=payment_intent.id,
                client_secret=payment_intent.client_secret or "",
                status=payment_intent.status,
                amount_minor=payment_intent.amount,
                currency=payment_intent.currency.upper(),
            )

        except stripe.StripeError as exc:
            logger.error(
                "stripe_payment_status_failed",
                payment_intent_id=payment_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise PaymentProviderError(f"Failed to get payment status: {exc}") from exc

    async def confirm_payment(self, payment_id: str) -> bool:
        """
        Confirm that a Stripe payment was successfully completed.

        Args:
            payment_id: Stripe PaymentIntent ID

        Returns:
            True if payment succeeded, False otherwise
        """
        try:
            logger.info("confirming_stripe_payment", payment_intent_id=payment_id)

            payment_intent = stripe.PaymentIntent.retrieve(payment_id)
            is_succeeded: bool = payment_intent.status == "succeeded"

            logger.info(
                "stripe_payment_confirmed",
                payment_intent_id=payment_id,
                status=payment_intent.status,
                succeeded=is_succeeded,
            )

            return is_succeeded

        except stripe.StripeError as exc:
            logger.error(
                "stripe_payment_confirmation_failed",
                payment_intent_id=payment_id,
                error=str(exc),
            )
            return False

    async def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """
        Verify and parse Stripe webhook event.

        Args:
            payload: Raw webhook payload
            signature: Stripe-Signature header value

        Returns:
            Parsed webhook event

        Raises:
            WebhookVerificationError: If signature verification fails
        """
        try:
            logger.info("verifying_stripe_webhook", signature_present=bool(signature))

            # Verify webhook signature
            event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
                payload, signature, self.webhook_secret
            )

            logger.info(
                "stripe_webhook_verified",
                event_id=event.id,
                event_type=event.type,
            )

            # Extract payment intent from event
            payment_intent = event.data.object

            # Parse webhook event into provider-agnostic format
            webhook_event = WebhookEvent(
                event_id=event.id,
                event_type=event.type,
                payment_id=payment_intent.id,
                status=payment_intent.status,
                amount_minor=payment_intent.get("amount"),
                currency=payment_intent.get("currency", "").upper()
                if payment_intent.get("currency")
                else None,
                metadata_account_id=payment_intent.get("metadata", {}).get("account_id"),
                metadata_oauth_provider=payment_intent.get("metadata", {}).get("oauth_provider"),
                metadata_external_id=payment_intent.get("metadata", {}).get("external_id"),
            )

            return webhook_event

        except stripe.SignatureVerificationError as exc:
            logger.error("stripe_webhook_verification_failed", error=str(exc))
            raise WebhookVerificationError("Invalid Stripe webhook signature") from exc
        except Exception as exc:
            logger.error("stripe_webhook_parsing_failed", error=str(exc))
            raise WebhookVerificationError(f"Failed to parse Stripe webhook: {exc}") from exc

    async def refund_payment(self, payment_id: str, amount_minor: int | None = None) -> str:
        """
        Refund a Stripe payment.

        Args:
            payment_id: Stripe PaymentIntent ID
            amount_minor: Amount to refund in minor units (None = full refund)

        Returns:
            Stripe Refund ID

        Raises:
            PaymentProviderError: If refund fails
        """
        try:
            logger.info(
                "creating_stripe_refund",
                payment_intent_id=payment_id,
                amount_minor=amount_minor,
            )

            # Create refund
            if amount_minor is not None:
                refund = stripe.Refund.create(
                    payment_intent=payment_id,
                    amount=amount_minor,
                )
            else:
                refund = stripe.Refund.create(payment_intent=payment_id)

            logger.info(
                "stripe_refund_created",
                refund_id=refund.id,
                status=refund.status,
                amount_minor=refund.amount,
            )

            refund_id: str = refund.id
            return refund_id

        except stripe.StripeError as exc:
            logger.error(
                "stripe_refund_failed",
                payment_intent_id=payment_id,
                error=str(exc),
            )
            raise PaymentProviderError(f"Stripe refund failed: {exc}") from exc
