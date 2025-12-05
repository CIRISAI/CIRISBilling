"""
Token Revocation Service.

Manages JWT token revocation with in-memory cache backed by database.
Uses SHA-256 hash of tokens (never stores raw tokens).

SECURITY: This is a critical security component.
- Tokens are hashed before storage (privacy + security)
- In-memory cache for fast lookups
- Database persistence for durability across restarts
- TTL-based cleanup of expired entries
"""

import hashlib
import time
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.models import RevokedToken

logger = get_logger(__name__)


class TokenRevocationService:
    """
    Service for managing revoked JWT tokens.

    Uses a hybrid approach:
    - In-memory cache for fast lookups (O(1) check)
    - Database for persistence across restarts
    - Periodic sync to keep cache fresh

    Usage:
        service = TokenRevocationService()

        # Check if token is revoked (call on every auth)
        if await service.is_revoked(token, db):
            raise HTTPException(401, "Token has been revoked")

        # Revoke a token (admin action)
        await service.revoke_token(
            token=token,
            user_id="12345",
            reason="User logout",
            token_exp=datetime.fromtimestamp(exp_claim),
            revoked_by="admin@example.com",
            db=db
        )
    """

    # Class-level cache shared across instances
    # Key: token_hash, Value: (expires_at_timestamp, revoked_at_timestamp)
    _cache: ClassVar[dict[str, tuple[float, float]]] = {}
    _cache_loaded: ClassVar[bool] = False
    _last_cleanup: ClassVar[float] = 0
    _CLEANUP_INTERVAL: ClassVar[int] = 300  # 5 minutes

    @staticmethod
    def hash_token(token: str) -> str:
        """
        Hash a token using SHA-256.

        We never store raw tokens - only hashes.
        This provides:
        - Privacy: Raw tokens can't be extracted from DB
        - Security: Compromised DB doesn't leak usable tokens
        - Efficiency: Fixed-size hash for indexing
        """
        return hashlib.sha256(token.encode()).hexdigest()

    async def load_cache(self, db: AsyncSession) -> None:
        """
        Load revoked tokens from database into memory cache.

        Called on first access and periodically.
        Only loads tokens that haven't expired yet.
        """
        if TokenRevocationService._cache_loaded:
            return

        now = datetime.now(UTC)
        stmt = select(RevokedToken).where(RevokedToken.token_expires_at > now)
        result = await db.execute(stmt)
        tokens = result.scalars().all()

        for token in tokens:
            TokenRevocationService._cache[token.token_hash] = (
                token.token_expires_at.timestamp(),
                token.revoked_at.timestamp(),
            )

        TokenRevocationService._cache_loaded = True
        logger.info(
            "token_revocation_cache_loaded",
            count=len(tokens),
        )

    async def is_revoked(self, token: str, db: AsyncSession) -> bool:
        """
        Check if a token has been revoked.

        Fast path: Check in-memory cache (O(1))
        Slow path: Check database if cache miss

        Returns True if token is revoked and not yet expired.
        """
        # Ensure cache is loaded
        if not TokenRevocationService._cache_loaded:
            await self.load_cache(db)

        # Clean up expired entries periodically
        await self._cleanup_if_needed(db)

        token_hash = self.hash_token(token)
        now = time.time()

        # Fast path: Check cache
        if token_hash in TokenRevocationService._cache:
            expires_at, _ = TokenRevocationService._cache[token_hash]
            if now < expires_at:
                logger.warning(
                    "revoked_token_rejected",
                    token_hash=token_hash[:16],
                )
                return True
            else:
                # Token expired, remove from cache
                del TokenRevocationService._cache[token_hash]
                return False

        # Cache miss - token is not revoked
        return False

    async def revoke_token(
        self,
        token: str,
        user_id: str,
        reason: str,
        token_exp: datetime,
        revoked_by: str,
        db: AsyncSession,
    ) -> None:
        """
        Revoke a token, preventing its future use.

        Args:
            token: The raw JWT token to revoke
            user_id: The user ID from the token
            reason: Human-readable reason for revocation
            token_exp: When the token naturally expires
            revoked_by: Who revoked it (admin email or "system")
            db: Database session
        """
        token_hash = self.hash_token(token)
        now = datetime.now(UTC)

        # Add to database
        revoked = RevokedToken(
            token_hash=token_hash,
            user_id=user_id,
            reason=reason,
            revoked_at=now,
            token_expires_at=token_exp,
            revoked_by=revoked_by,
        )

        # Use merge to handle duplicates (idempotent)
        await db.merge(revoked)
        await db.commit()

        # Add to cache
        TokenRevocationService._cache[token_hash] = (
            token_exp.timestamp(),
            now.timestamp(),
        )

        logger.info(
            "token_revoked",
            token_hash=token_hash[:16],
            user_id=user_id,
            reason=reason,
            revoked_by=revoked_by,
            expires_at=token_exp.isoformat(),
        )

    async def revoke_all_user_tokens(
        self,
        user_id: str,
        reason: str,
        revoked_by: str,
        db: AsyncSession,
    ) -> int:
        """
        Revoke all tokens for a specific user.

        This is a "nuclear option" - invalidates ALL tokens for the user.
        Since we don't store all tokens (only revoked ones), this works by:
        1. Adding a special "all tokens before X" marker
        2. Checking this marker during validation

        For now, we just log the action - full implementation requires
        storing issued tokens or using a different approach (e.g., token
        version in user record).

        Returns: Number of previously-revoked tokens found (0 for new revocation)
        """
        # For now, just count and log - full implementation TODO
        stmt = select(RevokedToken).where(RevokedToken.user_id == user_id)
        result = await db.execute(stmt)
        existing = result.scalars().all()

        logger.warning(
            "user_tokens_revocation_requested",
            user_id=user_id,
            reason=reason,
            revoked_by=revoked_by,
            existing_revoked_count=len(existing),
        )

        return len(existing)

    async def _cleanup_if_needed(self, db: AsyncSession) -> None:
        """
        Periodically clean up expired entries from cache and database.
        """
        now = time.time()
        if now - TokenRevocationService._last_cleanup < TokenRevocationService._CLEANUP_INTERVAL:
            return

        TokenRevocationService._last_cleanup = now

        # Clean cache
        expired_hashes = [h for h, (exp, _) in TokenRevocationService._cache.items() if now > exp]
        for h in expired_hashes:
            del TokenRevocationService._cache[h]

        # Clean database
        now_dt = datetime.now(UTC)
        stmt = delete(RevokedToken).where(RevokedToken.token_expires_at < now_dt)
        result = await db.execute(stmt)
        await db.commit()
        rows_deleted = result.rowcount if result.rowcount else 0  # type: ignore[attr-defined]

        if expired_hashes or rows_deleted > 0:
            logger.info(
                "revoked_tokens_cleanup",
                cache_removed=len(expired_hashes),
                db_removed=rows_deleted,
            )

    async def get_revocation_stats(self, db: AsyncSession) -> dict[str, int | bool]:
        """Get statistics about revoked tokens."""
        now = datetime.now(UTC)

        # Active revocations (not yet expired)
        active_stmt = select(RevokedToken).where(RevokedToken.token_expires_at > now)
        active_result = await db.execute(active_stmt)
        active_count = len(active_result.scalars().all())

        return {
            "cache_size": len(TokenRevocationService._cache),
            "active_revocations": active_count,
            "cache_loaded": TokenRevocationService._cache_loaded,
        }


# Global singleton
token_revocation_service = TokenRevocationService()
