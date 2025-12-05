"""
Google Play Provider Implementation.

NO DICTIONARIES - All data uses strongly typed models.
"""

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from structlog import get_logger

from app.exceptions import PaymentProviderError, WebhookVerificationError
from app.models.google_play import (
    GooglePlayPurchaseToken,
    GooglePlayPurchaseVerification,
    GooglePlayWebhookEvent,
)

logger = get_logger(__name__)


class GooglePlayProvider:
    """
    Google Play In-App Billing provider.

    Handles purchase verification, consumption, and webhook processing.
    """

    def __init__(
        self,
        service_account_json: str | dict[str, str],
        package_name: str,
    ) -> None:
        """
        Initialize Google Play provider.

        Args:
            service_account_json: Path to service account JSON or dict with credentials
            package_name: Android package name (e.g., 'ai.ciris.agent')
        """
        self.package_name = package_name

        # Load service account credentials
        if isinstance(service_account_json, str):
            self.credentials = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                service_account_json,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )
        else:
            self.credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                service_account_json,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )

        # Build API client
        self.service = build(
            "androidpublisher", "v3", credentials=self.credentials, cache_discovery=False
        )

        logger.info("google_play_provider_initialized", package_name=package_name)

    async def verify_purchase(
        self,
        purchase_token: GooglePlayPurchaseToken,
    ) -> GooglePlayPurchaseVerification:
        """
        Verify a one-time product purchase with Google Play.

        Args:
            purchase_token: Validated purchase token

        Returns:
            Purchase verification result

        Raises:
            PaymentProviderError: If verification fails
        """
        try:
            logger.info(
                "verifying_google_play_purchase",
                product_id=purchase_token.product_id,
                package_name=purchase_token.package_name,
            )

            # Call Google Play API
            result = (
                self.service.purchases()
                .products()
                .get(
                    packageName=purchase_token.package_name,
                    productId=purchase_token.product_id,
                    token=purchase_token.token,
                )
                .execute()
            )

            # purchaseType: None=real purchase, 0=test, 1=promo, 2=rewarded
            purchase_type = result.get("purchaseType")
            if purchase_type is not None:
                purchase_type = int(purchase_type)

            logger.info(
                "google_play_purchase_verified",
                order_id=result.get("orderId"),
                product_id=purchase_token.product_id,
                purchase_state=result.get("purchaseState"),
                purchase_type=purchase_type,
                is_test=purchase_type == 0,
            )

            return GooglePlayPurchaseVerification(
                order_id=result["orderId"],
                purchase_token=purchase_token.token,
                product_id=purchase_token.product_id,
                package_name=purchase_token.package_name,
                purchase_time_millis=int(result["purchaseTimeMillis"]),
                purchase_state=int(result["purchaseState"]),
                acknowledgement_state=int(result.get("acknowledgementState", 0)),
                consumption_state=int(result.get("consumptionState", 0)),
                purchase_type=purchase_type,
            )

        except HttpError as exc:
            error_content = exc.content.decode("utf-8") if exc.content else str(exc)
            logger.error(
                "google_play_verification_failed",
                status=exc.resp.status,
                error=error_content,
            )

            if exc.resp.status == 404:
                raise PaymentProviderError("Purchase not found or invalid token") from exc
            elif exc.resp.status == 410:
                raise PaymentProviderError("Purchase token expired") from exc
            else:
                raise PaymentProviderError(f"Google Play API error: {error_content}") from exc

        except Exception as exc:
            logger.exception("google_play_verification_unexpected_error")
            raise PaymentProviderError(f"Verification failed: {exc}") from exc

    async def consume_purchase(
        self,
        purchase_token: str,
        product_id: str,
    ) -> None:
        """
        Consume a one-time product purchase (marks as used).

        Args:
            purchase_token: Purchase token
            product_id: Product ID

        Raises:
            PaymentProviderError: If consumption fails
        """
        try:
            logger.info("consuming_google_play_purchase", product_id=product_id)

            self.service.purchases().products().consume(
                packageName=self.package_name,
                productId=product_id,
                token=purchase_token,
            ).execute()

            logger.info("google_play_purchase_consumed", product_id=product_id)

        except HttpError as exc:
            error_content = exc.content.decode("utf-8") if exc.content else str(exc)
            logger.error(
                "google_play_consumption_failed",
                product_id=product_id,
                error=error_content,
            )
            raise PaymentProviderError(f"Consumption failed: {error_content}") from exc

    async def acknowledge_purchase(
        self,
        purchase_token: str,
        product_id: str,
    ) -> None:
        """
        Acknowledge a purchase (required within 3 days).

        Args:
            purchase_token: Purchase token
            product_id: Product ID

        Raises:
            PaymentProviderError: If acknowledgement fails
        """
        try:
            logger.info("acknowledging_google_play_purchase", product_id=product_id)

            self.service.purchases().products().acknowledge(
                packageName=self.package_name,
                productId=product_id,
                token=purchase_token,
            ).execute()

            logger.info("google_play_purchase_acknowledged", product_id=product_id)

        except HttpError as exc:
            error_content = exc.content.decode("utf-8") if exc.content else str(exc)
            logger.error(
                "google_play_acknowledgement_failed",
                product_id=product_id,
                error=error_content,
            )
            raise PaymentProviderError(f"Acknowledgement failed: {error_content}") from exc

    async def verify_webhook(
        self,
        payload: bytes,
    ) -> GooglePlayWebhookEvent:
        """
        Verify Google Play Real-Time Developer Notification webhook.

        Args:
            payload: Raw webhook payload (JSON from Pub/Sub)

        Returns:
            Parsed webhook event

        Raises:
            WebhookVerificationError: If verification fails
        """
        import base64
        import json

        try:
            logger.info("verifying_google_play_webhook")

            # Parse Pub/Sub message
            pubsub_message = json.loads(payload)

            # Extract base64-encoded message data
            message_data = pubsub_message.get("message", {}).get("data")
            if not message_data:
                raise WebhookVerificationError("No message data in webhook")

            # Decode base64
            decoded_data = base64.b64decode(message_data).decode("utf-8")
            notification = json.loads(decoded_data)

            logger.info(
                "google_play_webhook_verified",
                version=notification.get("version"),
                package_name=notification.get("packageName"),
            )

            # Extract notification details
            product_notification = notification.get("oneTimeProductNotification", {})
            purchase_token = product_notification.get("purchaseToken", "")
            product_id = product_notification.get("sku", "")
            notification_type = product_notification.get("notificationType", 0)

            return GooglePlayWebhookEvent(
                event_id=pubsub_message.get("message", {}).get("messageId", ""),
                event_type=self._get_event_type(notification_type),
                purchase_token=purchase_token,
                product_id=product_id,
                package_name=notification.get("packageName", ""),
                notification_type=notification_type,
                event_time_millis=int(notification.get("eventTimeMillis", 0)),
            )

        except json.JSONDecodeError as exc:
            logger.error("google_play_webhook_invalid_json", error=str(exc))
            raise WebhookVerificationError("Invalid JSON payload") from exc
        except Exception as exc:
            logger.exception("google_play_webhook_verification_failed")
            raise WebhookVerificationError(f"Webhook verification failed: {exc}") from exc

    def _get_event_type(self, notification_type: int) -> str:
        """Map notification type to event type string."""
        event_types = {
            1: "product_purchased",
            2: "product_canceled",
        }
        return event_types.get(notification_type, f"unknown_{notification_type}")
