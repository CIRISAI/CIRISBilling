"""
Payment Provider Protocol - Provider-agnostic interface.

NO DICTIONARIES - All data uses strongly typed models.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PaymentIntent:
    """
    Provider-agnostic payment intent.

    Represents a request to create a payment.
    """

    amount_minor: int
    currency: str
    description: str
    customer_email: str
    metadata_account_id: str
    metadata_external_id: str
    idempotency_key: str


@dataclass(frozen=True)
class PaymentResult:
    """
    Provider-agnostic payment result.

    Returned after successful payment creation.
    """

    payment_id: str  # Provider-specific payment ID
    client_secret: str  # For client-side payment confirmation
    status: str
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class WebhookEvent:
    """
    Provider-agnostic webhook event.

    Represents a webhook notification from payment provider.
    """

    event_id: str
    event_type: str
    payment_id: str
    status: str
    amount_minor: int | None
    currency: str | None
    metadata_account_id: str | None


class PaymentProvider(Protocol):
    """
    Payment provider protocol.

    Any payment provider (Stripe, Square, PayPal, etc.) must implement this interface.
    This ensures CIRIS Billing remains provider-agnostic.
    """

    async def create_payment_intent(self, intent: PaymentIntent) -> PaymentResult:
        """
        Create a payment intent with the provider.

        Args:
            intent: Payment intent details

        Returns:
            Payment result with provider-specific payment ID and client secret

        Raises:
            PaymentProviderError: If payment creation fails
        """
        ...

    async def confirm_payment(self, payment_id: str) -> bool:
        """
        Confirm that a payment was successfully completed.

        Args:
            payment_id: Provider-specific payment ID

        Returns:
            True if payment succeeded, False otherwise
        """
        ...

    async def verify_webhook(self, payload: bytes, signature: str) -> WebhookEvent:
        """
        Verify and parse webhook event from provider.

        Args:
            payload: Raw webhook payload
            signature: Webhook signature for verification

        Returns:
            Parsed webhook event

        Raises:
            WebhookVerificationError: If signature verification fails
        """
        ...

    async def refund_payment(self, payment_id: str, amount_minor: int | None = None) -> str:
        """
        Refund a payment.

        Args:
            payment_id: Provider-specific payment ID
            amount_minor: Amount to refund (None = full refund)

        Returns:
            Refund ID

        Raises:
            PaymentProviderError: If refund fails
        """
        ...
