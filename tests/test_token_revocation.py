"""
Tests for Token Revocation Service.

Tests token hashing, revocation, and cache management.
"""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import RevokedToken
from app.services.token_revocation import TokenRevocationService


class TestTokenHashing:
    """Tests for token hashing."""

    def test_hash_token_returns_hex_string(self):
        """hash_token returns a hex string."""
        service = TokenRevocationService()
        result = service.hash_token("test_token")
        assert isinstance(result, str)
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_token_is_sha256(self):
        """hash_token uses SHA-256."""
        service = TokenRevocationService()
        token = "my_jwt_token"
        result = service.hash_token(token)

        expected = hashlib.sha256(token.encode()).hexdigest()
        assert result == expected

    def test_hash_token_deterministic(self):
        """Same token always produces same hash."""
        service = TokenRevocationService()
        token = "consistent_token"

        hash1 = service.hash_token(token)
        hash2 = service.hash_token(token)

        assert hash1 == hash2

    def test_hash_token_different_tokens_different_hashes(self):
        """Different tokens produce different hashes."""
        service = TokenRevocationService()

        hash1 = service.hash_token("token1")
        hash2 = service.hash_token("token2")

        assert hash1 != hash2

    def test_hash_token_length(self):
        """SHA-256 hash is 64 characters."""
        service = TokenRevocationService()
        result = service.hash_token("any_token")
        assert len(result) == 64


class TestIsRevoked:
    """Tests for is_revoked method."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = time.time()  # Prevent cleanup
        yield
        # Clean up after test
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_not_revoked_returns_false(self):
        """Non-revoked token returns False."""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db.execute = AsyncMock(return_value=mock_result)

        # Mark cache as loaded to skip load_cache
        TokenRevocationService._cache_loaded = True

        service = TokenRevocationService()
        result = await service.is_revoked("valid_token", db)

        assert result is False

    @pytest.mark.asyncio
    async def test_revoked_in_cache_returns_true(self):
        """Token in cache returns True."""
        token = "revoked_token"
        token_hash = TokenRevocationService.hash_token(token)

        # Pre-populate cache with non-expired entry
        future_time = time.time() + 3600  # 1 hour from now
        TokenRevocationService._cache[token_hash] = (future_time, time.time())
        TokenRevocationService._cache_loaded = True
        TokenRevocationService._last_cleanup = time.time()  # Prevent cleanup

        db = AsyncMock()
        service = TokenRevocationService()
        result = await service.is_revoked(token, db)

        assert result is True

    @pytest.mark.asyncio
    async def test_expired_revocation_returns_false(self):
        """Expired revocation in cache returns False and removes entry."""
        token = "expired_revoked_token"
        token_hash = TokenRevocationService.hash_token(token)

        # Pre-populate cache with expired entry
        past_time = time.time() - 3600  # 1 hour ago
        TokenRevocationService._cache[token_hash] = (past_time, past_time - 7200)
        TokenRevocationService._cache_loaded = True

        db = AsyncMock()
        service = TokenRevocationService()
        result = await service.is_revoked(token, db)

        assert result is False
        assert token_hash not in TokenRevocationService._cache  # Removed from cache


class TestRevokeToken:
    """Tests for revoke_token method."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = True  # Skip load_cache
        TokenRevocationService._last_cleanup = time.time()  # Skip cleanup
        yield
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_revoke_adds_to_database(self):
        """Revoking token adds to database."""
        db = AsyncMock()
        db.merge = AsyncMock()
        db.commit = AsyncMock()

        service = TokenRevocationService()
        token = "token_to_revoke"
        user_id = "user123"
        reason = "User logout"
        token_exp = datetime.now(UTC) + timedelta(hours=1)
        revoked_by = "admin@example.com"

        await service.revoke_token(
            token=token,
            user_id=user_id,
            reason=reason,
            token_exp=token_exp,
            revoked_by=revoked_by,
            db=db,
        )

        db.merge.assert_called_once()
        db.commit.assert_called_once()

        # Check that RevokedToken was created with correct values
        call_args = db.merge.call_args[0][0]
        assert isinstance(call_args, RevokedToken)
        assert call_args.token_hash == service.hash_token(token)
        assert call_args.user_id == user_id
        assert call_args.reason == reason

    @pytest.mark.asyncio
    async def test_revoke_adds_to_cache(self):
        """Revoking token adds to cache."""
        db = AsyncMock()
        db.merge = AsyncMock()
        db.commit = AsyncMock()

        service = TokenRevocationService()
        token = "cache_test_token"
        token_exp = datetime.now(UTC) + timedelta(hours=1)

        await service.revoke_token(
            token=token,
            user_id="user",
            reason="test",
            token_exp=token_exp,
            revoked_by="admin",
            db=db,
        )

        token_hash = service.hash_token(token)
        assert token_hash in TokenRevocationService._cache
        expires_at, revoked_at = TokenRevocationService._cache[token_hash]
        assert expires_at == pytest.approx(token_exp.timestamp(), abs=1)


class TestLoadCache:
    """Tests for load_cache method."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0
        yield
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_load_cache_populates_cache(self):
        """load_cache populates cache from database."""
        db = AsyncMock()

        # Mock revoked tokens from DB
        token1 = MagicMock()
        token1.token_hash = "hash1"
        token1.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        token1.revoked_at = datetime.now(UTC)

        token2 = MagicMock()
        token2.token_hash = "hash2"
        token2.token_expires_at = datetime.now(UTC) + timedelta(hours=2)
        token2.revoked_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[token1, token2]))
        )
        db.execute = AsyncMock(return_value=mock_result)

        service = TokenRevocationService()
        await service.load_cache(db)

        assert TokenRevocationService._cache_loaded is True
        assert "hash1" in TokenRevocationService._cache
        assert "hash2" in TokenRevocationService._cache

    @pytest.mark.asyncio
    async def test_load_cache_skips_if_already_loaded(self):
        """load_cache skips if cache already loaded."""
        db = AsyncMock()
        TokenRevocationService._cache_loaded = True

        service = TokenRevocationService()
        await service.load_cache(db)

        db.execute.assert_not_called()


class TestRevokeAllUserTokens:
    """Tests for revoke_all_user_tokens method."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = True
        TokenRevocationService._last_cleanup = time.time()
        yield
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_returns_count_of_existing_revocations(self):
        """Returns count of already revoked tokens for user."""
        db = AsyncMock()

        # Mock existing revoked tokens
        mock_tokens = [MagicMock(), MagicMock(), MagicMock()]
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=mock_tokens))
        )
        db.execute = AsyncMock(return_value=mock_result)

        service = TokenRevocationService()
        result = await service.revoke_all_user_tokens(
            user_id="user123",
            reason="Account compromised",
            revoked_by="admin",
            db=db,
        )

        assert result == 3

    @pytest.mark.asyncio
    async def test_returns_zero_for_no_existing(self):
        """Returns 0 when user has no revoked tokens."""
        db = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db.execute = AsyncMock(return_value=mock_result)

        service = TokenRevocationService()
        result = await service.revoke_all_user_tokens(
            user_id="new_user",
            reason="test",
            revoked_by="admin",
            db=db,
        )

        assert result == 0


class TestGetRevocationStats:
    """Tests for get_revocation_stats method."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = True
        TokenRevocationService._last_cleanup = time.time()
        yield
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_returns_stats_dict(self):
        """Returns dictionary with stats."""
        db = AsyncMock()

        # Add some entries to cache
        TokenRevocationService._cache["hash1"] = (time.time() + 3600, time.time())
        TokenRevocationService._cache["hash2"] = (time.time() + 7200, time.time())

        # Mock active revocations from DB
        mock_tokens = [MagicMock(), MagicMock(), MagicMock()]
        mock_result = MagicMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=mock_tokens))
        )
        db.execute = AsyncMock(return_value=mock_result)

        service = TokenRevocationService()
        result = await service.get_revocation_stats(db)

        assert result["cache_size"] == 2
        assert result["active_revocations"] == 3
        assert result["cache_loaded"] is True


class TestCleanup:
    """Tests for cleanup functionality."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset class-level cache before each test."""
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = True
        TokenRevocationService._last_cleanup = 0
        yield
        TokenRevocationService._cache = {}
        TokenRevocationService._cache_loaded = False
        TokenRevocationService._last_cleanup = 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired_from_cache(self):
        """Cleanup removes expired entries from cache."""
        db = AsyncMock()

        # Mock delete result
        mock_result = MagicMock()
        mock_result.rowcount = 0
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        # Add expired and non-expired entries
        now = time.time()
        TokenRevocationService._cache["expired"] = (now - 100, now - 200)  # Expired
        TokenRevocationService._cache["valid"] = (now + 3600, now)  # Not expired
        TokenRevocationService._last_cleanup = 0  # Force cleanup

        service = TokenRevocationService()
        await service._cleanup_if_needed(db)

        assert "expired" not in TokenRevocationService._cache
        assert "valid" in TokenRevocationService._cache

    @pytest.mark.asyncio
    async def test_cleanup_skipped_if_recent(self):
        """Cleanup is skipped if done recently."""
        db = AsyncMock()

        # Set last cleanup to recent time
        TokenRevocationService._last_cleanup = time.time()

        service = TokenRevocationService()
        await service._cleanup_if_needed(db)

        # Database should not be called
        db.execute.assert_not_called()
