"""
Apple StoreKit Provider Implementation.

NO DICTIONARIES - All data uses strongly typed models.

Uses Apple App Store Server API v2 for transaction verification.
https://developer.apple.com/documentation/appstoreserverapi
"""

import base64
import binascii
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from structlog import get_logger

from app.exceptions import PaymentProviderError, WebhookVerificationError
from app.models.apple_storekit import (
    AppleRenewalInfo,
    AppleStoreKitConfig,
    AppleStoreKitWebhookEvent,
    AppleTransactionInfo,
)

logger = get_logger(__name__)

# SHA-256 fingerprint of Apple Root CA - G3, the trust anchor for App Store
# Server signing. Pinning by fingerprint is self-contained and removes the
# need to ship the cert file with the deployment.
# Source: https://www.apple.com/certificateauthority/
APPLE_ROOT_CA_G3_SHA256 = bytes.fromhex(
    "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179"
)


def _verify_apple_jws(signed_data: str) -> dict[str, Any]:
    """
    Verify a JWS signed by Apple, returning the decoded payload.

    Steps:
      1. Parse the JWS protected header; require ES256 and an x5c chain.
      2. Load the x5c chain (DER, base64-encoded), build leaf → root.
      3. Verify each cert is within its validity period and is signed by
         its issuer in the chain.
      4. Pin the root: the topmost cert MUST match Apple Root CA - G3 by
         SHA-256 fingerprint.
      5. Verify the JWS signature against the leaf cert's public key.

    Raises WebhookVerificationError on any failure. The exception messages
    are intentionally non-leaky.
    """
    try:
        header = jwt.get_unverified_header(signed_data)
    except jwt.exceptions.DecodeError as exc:
        raise WebhookVerificationError("Malformed JWS header") from exc

    alg = header.get("alg")
    if alg != "ES256":
        raise WebhookVerificationError(f"Unexpected JWS algorithm: {alg}")

    x5c_b64 = header.get("x5c")
    if not isinstance(x5c_b64, list) or not x5c_b64:
        raise WebhookVerificationError("JWS header missing x5c chain")

    # Decode each cert. x5c entries are standard base64 (NOT URL-safe), per RFC 7515 §4.1.6.
    try:
        chain = [x509.load_der_x509_certificate(base64.b64decode(entry)) for entry in x5c_b64]
    except (ValueError, TypeError, binascii.Error) as exc:
        raise WebhookVerificationError("Could not parse x5c chain") from exc

    # Validity periods. We use UTC-aware variants (deprecated naive ones removed in 46+).
    now = datetime.now(UTC)
    for cert in chain:
        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        if now < not_before or now > not_after:
            raise WebhookVerificationError("Certificate outside validity period")

    # Pin the root: the topmost cert must match Apple Root CA - G3.
    root = chain[-1]
    root_fingerprint = root.fingerprint(hashes.SHA256())
    if root_fingerprint != APPLE_ROOT_CA_G3_SHA256:
        raise WebhookVerificationError("JWS chain not rooted at Apple Root CA - G3")

    # Verify each cert in the chain is signed by the next.
    for i in range(len(chain) - 1):
        try:
            chain[i].verify_directly_issued_by(chain[i + 1])
        except (ValueError, TypeError, x509.InvalidVersion) as exc:
            raise WebhookVerificationError(f"Chain link {i} not signed by issuer") from exc
        except Exception as exc:  # cryptography raises various subclasses
            raise WebhookVerificationError(f"Chain link {i} verification failed") from exc

    # Verify the JWS signature using the leaf cert's public key.
    leaf = chain[0]
    leaf_pem = leaf.public_key().public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        payload: dict[str, Any] = jwt.decode(
            signed_data,
            leaf_pem,
            algorithms=["ES256"],
            options={"verify_aud": False, "verify_iss": False, "verify_exp": False},
        )
    except jwt.exceptions.InvalidSignatureError as exc:
        raise WebhookVerificationError("JWS signature does not verify") from exc
    except jwt.exceptions.InvalidTokenError as exc:
        raise WebhookVerificationError("JWS payload invalid") from exc

    return payload


class AppleStoreKitProvider:
    """
    Apple App Store Server API provider.

    Handles purchase verification, transaction lookup, and webhook processing.
    """

    def __init__(self, config: AppleStoreKitConfig) -> None:
        """
        Initialize Apple StoreKit provider.

        Args:
            config: StoreKit configuration with API credentials
        """
        self.config = config
        self._jwt_token: str | None = None
        self._jwt_expires_at: float = 0

        logger.info(
            "apple_storekit_provider_initialized",
            bundle_id=config.bundle_id,
            environment=config.environment,
        )

    def _generate_jwt(self) -> str:
        """
        Generate JWT for App Store Server API authentication.

        The JWT is valid for up to 60 minutes.
        """
        now = time.time()

        # Reuse cached token if still valid (with 5 min buffer)
        if self._jwt_token and now < (self._jwt_expires_at - 300):
            return self._jwt_token

        # Decode private key if base64 encoded
        private_key = self.config.private_key
        try:
            # Try to decode as base64
            decoded = base64.b64decode(private_key)
            private_key = decoded.decode("utf-8")
        except Exception:
            # Already plain text
            pass

        # Build JWT payload
        expires_at = now + 3600  # 1 hour
        payload = {
            "iss": self.config.issuer_id,
            "iat": int(now),
            "exp": int(expires_at),
            "aud": "appstoreconnect-v1",
            "bid": self.config.bundle_id,
        }

        # Sign JWT with ES256 (Apple requires this algorithm)
        token = jwt.encode(
            payload,
            private_key,
            algorithm="ES256",
            headers={"kid": self.config.key_id},
        )

        self._jwt_token = token
        self._jwt_expires_at = expires_at

        return token

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Make authenticated request to App Store Server API."""
        url = f"{self.config.api_base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._generate_jwt()}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                timeout=30.0,
                **kwargs,  # type: ignore[arg-type]
            )

            if response.status_code == 401:
                raise PaymentProviderError("Invalid API credentials")
            elif response.status_code == 404:
                raise PaymentProviderError("Transaction not found")
            elif response.status_code >= 400:
                error_body = response.text
                logger.error(
                    "apple_storekit_api_error",
                    status=response.status_code,
                    error=error_body,
                )
                raise PaymentProviderError(f"API error: {response.status_code}")

            result: dict[str, Any] = response.json()
            return result

    def _decode_jws(self, signed_data: str) -> dict[str, Any]:
        """
        Decode and verify JWS signed data from Apple.

        Always validates the x5c certificate chain against Apple Root CA - G3
        and verifies the JWS signature with the leaf cert's public key. Used
        for both inbound webhooks AND outbound App Store Server API responses
        — neither path trusts Apple-signed bytes without cryptographic proof.
        See docs/THREAT_MODEL.md AV-4.
        """
        try:
            return _verify_apple_jws(signed_data)
        except WebhookVerificationError as e:
            raise PaymentProviderError(f"JWS verification failed: {e}") from e

    def _parse_transaction_info(self, data: dict[str, Any]) -> AppleTransactionInfo:
        """Parse transaction info from decoded JWS payload."""

        def parse_timestamp(ms: int | None) -> datetime:
            if ms is None:
                return datetime.now(UTC)
            return datetime.fromtimestamp(ms / 1000, tz=UTC)

        return AppleTransactionInfo(
            transaction_id=data["transactionId"],
            original_transaction_id=data["originalTransactionId"],
            product_id=data["productId"],
            bundle_id=data["bundleId"],
            purchase_date=parse_timestamp(data.get("purchaseDate")),
            original_purchase_date=parse_timestamp(data.get("originalPurchaseDate")),
            quantity=data.get("quantity", 1),
            type=data.get("type", "Consumable"),
            environment=data.get("environment", "Production"),
            storefront=data.get("storefront", ""),
            storefront_id=data.get("storefrontId", ""),
            app_account_token=data.get("appAccountToken"),
            in_app_ownership_type=data.get("inAppOwnershipType"),
            expires_date=parse_timestamp(data.get("expiresDate"))
            if data.get("expiresDate")
            else None,
            revocation_date=parse_timestamp(data.get("revocationDate"))
            if data.get("revocationDate")
            else None,
            revocation_reason=data.get("revocationReason"),
            is_upgraded=data.get("isUpgraded", False),
        )

    async def get_transaction_info(
        self,
        transaction_id: str,
    ) -> AppleTransactionInfo:
        """
        Get transaction information from App Store Server API.

        Args:
            transaction_id: The transaction ID to look up

        Returns:
            Transaction information

        Raises:
            PaymentProviderError: If lookup fails
        """
        logger.info(
            "getting_apple_transaction_info",
            transaction_id=transaction_id,
        )

        try:
            result = await self._make_request(
                "GET",
                f"/inApps/v1/transactions/{transaction_id}",
            )

            # Result contains signedTransactionInfo as JWS
            signed_data = result.get("signedTransactionInfo")
            if not signed_data:
                raise PaymentProviderError("No transaction info in response")

            # Decode the JWS
            transaction_data = self._decode_jws(signed_data)
            transaction = self._parse_transaction_info(transaction_data)

            logger.info(
                "apple_transaction_info_retrieved",
                transaction_id=transaction.transaction_id,
                product_id=transaction.product_id,
                environment=transaction.environment,
            )

            return transaction

        except PaymentProviderError:
            raise
        except Exception as exc:
            logger.exception("apple_transaction_lookup_failed")
            raise PaymentProviderError(f"Transaction lookup failed: {exc}") from exc

    async def get_transaction_history(
        self,
        original_transaction_id: str,
    ) -> list[AppleTransactionInfo]:
        """
        Get all transactions for an original transaction ID.

        Useful for subscription history.

        Args:
            original_transaction_id: The original transaction ID

        Returns:
            List of transactions
        """
        logger.info(
            "getting_apple_transaction_history",
            original_transaction_id=original_transaction_id,
        )

        transactions: list[AppleTransactionInfo] = []
        revision: str | None = None

        try:
            while True:
                endpoint = f"/inApps/v1/history/{original_transaction_id}"
                if revision:
                    endpoint += f"?revision={revision}"

                result = await self._make_request("GET", endpoint)

                # Parse signed transactions
                signed_transactions = result.get("signedTransactions", [])
                for signed_data in signed_transactions:
                    tx_data = self._decode_jws(signed_data)
                    transactions.append(self._parse_transaction_info(tx_data))

                # Check for more pages
                if not result.get("hasMore", False):
                    break
                revision = result.get("revision")

            logger.info(
                "apple_transaction_history_retrieved",
                original_transaction_id=original_transaction_id,
                count=len(transactions),
            )

            return transactions

        except PaymentProviderError:
            raise
        except Exception as exc:
            logger.exception("apple_transaction_history_failed")
            raise PaymentProviderError(f"History lookup failed: {exc}") from exc

    async def verify_webhook(self, payload: bytes) -> AppleStoreKitWebhookEvent:
        """
        Verify and parse App Store Server Notification V2.

        Every nested JWS is verified against Apple Root CA - G3.

        Args:
            payload: Raw webhook payload (JWS signed)

        Returns:
            Parsed webhook event

        Raises:
            WebhookVerificationError: If verification fails
        """
        import json

        try:
            logger.info("verifying_apple_storekit_webhook")

            # Parse the outer JWS
            body = json.loads(payload)
            signed_payload = body.get("signedPayload")
            if not signed_payload:
                raise WebhookVerificationError("No signedPayload in webhook")

            # Decode the notification (contains nested JWS for transaction/renewal).
            # All three nested JWS payloads are signed by the same Apple chain;
            # verifying each independently catches mid-payload tampering.
            try:
                notification = self._decode_jws(signed_payload)
            except PaymentProviderError as exc:
                raise WebhookVerificationError(str(exc)) from exc

            # Parse transaction info if present
            transaction_info: AppleTransactionInfo | None = None
            signed_transaction = notification.get("data", {}).get("signedTransactionInfo")
            if signed_transaction:
                try:
                    tx_data = self._decode_jws(signed_transaction)
                except PaymentProviderError as exc:
                    raise WebhookVerificationError(str(exc)) from exc
                transaction_info = self._parse_transaction_info(tx_data)

            # Parse renewal info if present
            renewal_info: AppleRenewalInfo | None = None
            signed_renewal = notification.get("data", {}).get("signedRenewalInfo")
            if signed_renewal:
                try:
                    renewal_data = self._decode_jws(signed_renewal)
                except PaymentProviderError as exc:
                    raise WebhookVerificationError(str(exc)) from exc
                renewal_info = AppleRenewalInfo(
                    original_transaction_id=renewal_data.get("originalTransactionId", ""),
                    product_id=renewal_data.get("productId", ""),
                    auto_renew_status=renewal_data.get("autoRenewStatus", 0),
                    expiration_intent=renewal_data.get("expirationIntent"),
                    grace_period_expires_date=None,  # Parse if needed
                    is_in_billing_retry_period=renewal_data.get("isInBillingRetryPeriod", False),
                    offer_identifier=renewal_data.get("offerIdentifier"),
                    offer_type=renewal_data.get("offerType"),
                    price_increase_status=renewal_data.get("priceIncreaseStatus"),
                )

            # Parse signed date
            signed_date_ms = notification.get("signedDate", 0)
            signed_date = datetime.fromtimestamp(signed_date_ms / 1000, tz=UTC)

            event = AppleStoreKitWebhookEvent(
                notification_type=notification.get("notificationType", ""),
                subtype=notification.get("subtype"),
                notification_uuid=notification.get("notificationUUID", ""),
                version=notification.get("version", "2.0"),
                signed_date=signed_date,
                transaction_info=transaction_info,
                environment=notification.get("data", {}).get("environment", "Production"),
                renewal_info=renewal_info,
            )

            logger.info(
                "apple_storekit_webhook_verified",
                notification_type=event.notification_type,
                subtype=event.subtype,
                transaction_id=transaction_info.transaction_id if transaction_info else None,
            )

            return event

        except json.JSONDecodeError as exc:
            logger.error("apple_storekit_webhook_invalid_json", error=str(exc))
            raise WebhookVerificationError("Invalid JSON payload") from exc
        except WebhookVerificationError:
            raise
        except Exception as exc:
            logger.exception("apple_storekit_webhook_verification_failed")
            raise WebhookVerificationError(f"Webhook verification failed: {exc}") from exc

    async def request_test_notification(self) -> str:
        """
        Request a test notification from Apple.

        Returns:
            Test notification token

        Raises:
            PaymentProviderError: If request fails
        """
        logger.info("requesting_apple_test_notification")

        try:
            result = await self._make_request(
                "POST",
                "/inApps/v1/notifications/test",
            )

            token = str(result.get("testNotificationToken", ""))
            logger.info(
                "apple_test_notification_requested",
                token=token[:20] + "..." if token else "",
            )

            return token

        except Exception as exc:
            logger.exception("apple_test_notification_request_failed")
            raise PaymentProviderError(f"Test notification request failed: {exc}") from exc
