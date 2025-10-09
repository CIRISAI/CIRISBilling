"""
API Key Service - Generation and validation of agent API keys.

NO DICTIONARIES - All data uses typed models/dataclasses.
"""

import secrets
import base64
from datetime import datetime, timezone, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.models import APIKey
from app.exceptions import AuthenticationError

logger = get_logger(__name__)


class APIKeyData:
    """Data class for API key information (NO DICTIONARIES)."""

    def __init__(
        self,
        key_id: UUID,
        name: str,
        key_prefix: str,
        environment: str,
        permissions: list[str],
        status: str,
        created_at: datetime,
        expires_at: datetime | None,
        last_used_at: datetime | None,
    ):
        self.key_id = key_id
        self.name = name
        self.key_prefix = key_prefix
        self.environment = environment
        self.permissions = permissions
        self.status = status
        self.created_at = created_at
        self.expires_at = expires_at
        self.last_used_at = last_used_at


class GeneratedAPIKey:
    """Data class for newly generated API key (includes plaintext, shown once)."""

    def __init__(
        self,
        key_id: UUID,
        plaintext_key: str,
        key_prefix: str,
        name: str,
        environment: str,
        permissions: list[str],
        created_at: datetime,
        expires_at: datetime | None,
    ):
        self.key_id = key_id
        self.plaintext_key = plaintext_key
        self.key_prefix = key_prefix
        self.name = name
        self.environment = environment
        self.permissions = permissions
        self.created_at = created_at
        self.expires_at = expires_at


class APIKeyService:
    """Service for API key management."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.password_hasher = PasswordHasher()

    def generate_api_key(self, environment: str = "live") -> tuple[str, str, str]:
        """
        Generate a new API key.

        Returns:
            tuple: (plaintext_key, key_hash, key_prefix)
        """
        # Generate cryptographically secure random bytes
        random_bytes = secrets.token_bytes(32)

        # Encode as URL-safe base64
        key_suffix = base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")

        # Format: cbk_{env}_{suffix}
        plaintext_key = f"cbk_{environment}_{key_suffix}"

        # Extract prefix for indexing (first 20 chars)
        key_prefix = plaintext_key[:20]

        # Hash for storage using Argon2id
        key_hash = self.password_hasher.hash(plaintext_key)

        return plaintext_key, key_hash, key_prefix

    async def create_api_key(
        self,
        name: str,
        created_by: UUID,
        environment: str = "live",
        description: str | None = None,
        permissions: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> GeneratedAPIKey:
        """
        Create a new API key and store in database.

        Args:
            name: Human-readable name (e.g., "Production Agent")
            created_by: Admin user ID who created the key
            environment: "test" or "live"
            description: Optional description
            permissions: List of permission strings
            expires_in_days: Optional expiration (None = never expires)

        Returns:
            GeneratedAPIKey with plaintext key (shown once!)
        """
        if permissions is None:
            permissions = ["billing:read", "billing:write"]

        # Generate key
        plaintext_key, key_hash, key_prefix = self.generate_api_key(environment)

        # Calculate expiration
        expires_at = None
        if expires_in_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

        # Create database record
        api_key = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            name=name,
            description=description,
            environment=environment,
            permissions=permissions,
            created_by_id=created_by,
            expires_at=expires_at,
            status="active",
        )

        self.db.add(api_key)
        await self.db.commit()
        await self.db.refresh(api_key)

        logger.info(
            "api_key_created",
            key_id=str(api_key.id),
            name=name,
            environment=environment,
            created_by=str(created_by),
        )

        return GeneratedAPIKey(
            key_id=api_key.id,
            plaintext_key=plaintext_key,
            key_prefix=key_prefix,
            name=name,
            environment=environment,
            permissions=permissions,
            created_at=api_key.created_at,
            expires_at=expires_at,
        )

    async def validate_api_key(
        self, provided_key: str, update_last_used: bool = True
    ) -> APIKeyData:
        """
        Validate an API key and return key metadata if valid.

        Args:
            provided_key: The API key from X-API-Key header
            update_last_used: Whether to update last_used_at (default True)

        Returns:
            APIKeyData if valid

        Raises:
            AuthenticationError if invalid or expired
        """
        # Validate format
        if not provided_key.startswith("cbk_"):
            logger.warning("api_key_invalid_format", prefix=provided_key[:10])
            raise AuthenticationError("Invalid API key format")

        # Extract prefix for lookup
        key_prefix = provided_key[:20]

        # Look up by prefix
        stmt = select(APIKey).where(
            APIKey.key_prefix == key_prefix, APIKey.status == "active"
        )
        result = await self.db.execute(stmt)
        api_key = result.scalar_one_or_none()

        if not api_key:
            logger.warning("api_key_not_found", prefix=key_prefix)
            raise AuthenticationError("Invalid API key")

        # Verify hash
        try:
            self.password_hasher.verify(api_key.key_hash, provided_key)
        except (VerifyMismatchError, InvalidHashError):
            logger.warning("api_key_hash_mismatch", key_id=str(api_key.id))
            raise AuthenticationError("Invalid API key")

        # Check expiration
        if api_key.expires_at and datetime.now(timezone.utc) > api_key.expires_at:
            # Auto-revoke expired key
            api_key.status = "revoked"
            await self.db.commit()
            logger.warning(
                "api_key_expired", key_id=str(api_key.id), expired_at=api_key.expires_at
            )
            raise AuthenticationError("API key expired")

        # Update last_used_at (async, non-blocking if desired)
        if update_last_used:
            api_key.last_used_at = datetime.now(timezone.utc)
            # Note: Could make this async background task for performance
            await self.db.commit()

        logger.info("api_key_validated", key_id=str(api_key.id), name=api_key.name)

        return APIKeyData(
            key_id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            environment=api_key.environment,
            permissions=api_key.permissions,
            status=api_key.status,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
            last_used_at=api_key.last_used_at,
        )

    async def revoke_api_key(self, key_id: UUID) -> None:
        """Revoke an API key."""
        stmt = select(APIKey).where(APIKey.id == key_id)
        result = await self.db.execute(stmt)
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise ValueError(f"API key not found: {key_id}")

        api_key.status = "revoked"
        await self.db.commit()

        logger.info("api_key_revoked", key_id=str(key_id), name=api_key.name)

    async def rotate_api_key(
        self, key_id: UUID, grace_period_hours: int = 24
    ) -> GeneratedAPIKey:
        """
        Rotate an API key (create new, mark old as rotating).

        Args:
            key_id: Existing key ID to rotate
            grace_period_hours: Hours before old key is revoked (default 24)

        Returns:
            GeneratedAPIKey with new key
        """
        # Get existing key
        stmt = select(APIKey).where(APIKey.id == key_id)
        result = await self.db.execute(stmt)
        old_key = result.scalar_one_or_none()

        if not old_key:
            raise ValueError(f"API key not found: {key_id}")

        # Create new key with same properties
        new_key = await self.create_api_key(
            name=old_key.name,
            created_by=old_key.created_by,
            environment=old_key.environment,
            description=old_key.description,
            permissions=old_key.permissions,
            expires_in_days=(
                (old_key.expires_at - datetime.now(timezone.utc)).days
                if old_key.expires_at
                else None
            ),
        )

        # Mark old key as rotating (will be auto-revoked after grace period)
        old_key.status = "rotating"
        old_key.metadata = {
            "rotated_to": str(new_key.key_id),
            "grace_period_until": (
                datetime.now(timezone.utc) + timedelta(hours=grace_period_hours)
            ).isoformat(),
        }
        await self.db.commit()

        logger.info(
            "api_key_rotated",
            old_key_id=str(key_id),
            new_key_id=str(new_key.key_id),
            grace_period_hours=grace_period_hours,
        )

        return new_key

    async def list_api_keys(self) -> list[APIKeyData]:
        """List all API keys (excluding revoked)."""
        stmt = select(APIKey).where(APIKey.status.in_(["active", "rotating"]))
        result = await self.db.execute(stmt)
        api_keys = result.scalars().all()

        return [
            APIKeyData(
                key_id=key.id,
                name=key.name,
                key_prefix=key.key_prefix,
                environment=key.environment,
                permissions=key.permissions,
                status=key.status,
                created_at=key.created_at,
                expires_at=key.expires_at,
                last_used_at=key.last_used_at,
            )
            for key in api_keys
        ]
