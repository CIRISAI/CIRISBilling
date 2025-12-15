"""
Tests for API Key Service.

Tests key generation, validation, and management.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from argon2 import PasswordHasher

from app.db.models import APIKey
from app.exceptions import AuthenticationError
from app.services.api_key import APIKeyData, APIKeyService, GeneratedAPIKey


class TestAPIKeyData:
    """Tests for APIKeyData data class."""

    def test_all_attributes_set(self):
        """All attributes are correctly set."""
        key_id = uuid4()
        created_at = datetime.now(UTC)
        expires_at = datetime.now(UTC) + timedelta(days=30)
        last_used = datetime.now(UTC)

        data = APIKeyData(
            key_id=key_id,
            name="Test Key",
            key_prefix="cbk_live_abc123",
            environment="live",
            permissions=["billing:read", "billing:write"],
            status="active",
            created_at=created_at,
            expires_at=expires_at,
            last_used_at=last_used,
        )

        assert data.key_id == key_id
        assert data.name == "Test Key"
        assert data.key_prefix == "cbk_live_abc123"
        assert data.environment == "live"
        assert data.permissions == ["billing:read", "billing:write"]
        assert data.status == "active"
        assert data.created_at == created_at
        assert data.expires_at == expires_at
        assert data.last_used_at == last_used

    def test_optional_fields_can_be_none(self):
        """Optional fields can be None."""
        data = APIKeyData(
            key_id=uuid4(),
            name="Test",
            key_prefix="cbk_live_abc",
            environment="live",
            permissions=[],
            status="active",
            created_at=datetime.now(UTC),
            expires_at=None,
            last_used_at=None,
        )

        assert data.expires_at is None
        assert data.last_used_at is None


class TestGeneratedAPIKey:
    """Tests for GeneratedAPIKey data class."""

    def test_all_attributes_set(self):
        """All attributes are correctly set."""
        key_id = uuid4()
        created_at = datetime.now(UTC)
        expires_at = datetime.now(UTC) + timedelta(days=30)

        key = GeneratedAPIKey(
            key_id=key_id,
            plaintext_key="cbk_live_abc123secret",
            key_prefix="cbk_live_abc123",
            name="Production Key",
            environment="live",
            permissions=["billing:read"],
            created_at=created_at,
            expires_at=expires_at,
        )

        assert key.key_id == key_id
        assert key.plaintext_key == "cbk_live_abc123secret"
        assert key.key_prefix == "cbk_live_abc123"
        assert key.name == "Production Key"
        assert key.environment == "live"
        assert key.permissions == ["billing:read"]
        assert key.created_at == created_at
        assert key.expires_at == expires_at


class TestAPIKeyServiceGeneration:
    """Tests for API key generation."""

    def test_generate_api_key_format_live(self):
        """Generated key has correct format for live environment."""
        session = AsyncMock()
        service = APIKeyService(session)

        plaintext, key_hash, prefix = service.generate_api_key("live")

        assert plaintext.startswith("cbk_live_")
        assert prefix == plaintext[:20]
        assert len(plaintext) > 20  # Has suffix after prefix

    def test_generate_api_key_format_test(self):
        """Generated key has correct format for test environment."""
        session = AsyncMock()
        service = APIKeyService(session)

        plaintext, key_hash, prefix = service.generate_api_key("test")

        assert plaintext.startswith("cbk_test_")
        assert prefix == plaintext[:20]

    def test_generate_api_key_unique(self):
        """Each generated key is unique."""
        session = AsyncMock()
        service = APIKeyService(session)

        keys = [service.generate_api_key("live")[0] for _ in range(10)]

        assert len(set(keys)) == 10  # All unique

    def test_generate_api_key_hash_verifiable(self):
        """Generated hash can be verified."""
        session = AsyncMock()
        service = APIKeyService(session)

        plaintext, key_hash, _ = service.generate_api_key("live")

        # Verify the hash
        hasher = PasswordHasher()
        hasher.verify(key_hash, plaintext)  # Should not raise

    def test_generate_api_key_prefix_length(self):
        """Prefix is exactly 20 characters."""
        session = AsyncMock()
        service = APIKeyService(session)

        _, _, prefix = service.generate_api_key("live")

        assert len(prefix) == 20


class TestAPIKeyServiceValidation:
    """Tests for API key validation."""

    @pytest.mark.asyncio
    async def test_invalid_format_no_prefix(self):
        """Keys without cbk_ prefix are rejected."""
        session = AsyncMock()
        service = APIKeyService(session)

        with pytest.raises(AuthenticationError) as exc:
            await service.validate_api_key("invalid_key_format")

        assert "Invalid API key format" in str(exc.value)

    @pytest.mark.asyncio
    async def test_invalid_format_wrong_prefix(self):
        """Keys with wrong prefix are rejected."""
        session = AsyncMock()
        service = APIKeyService(session)

        with pytest.raises(AuthenticationError) as exc:
            await service.validate_api_key("abc_live_something")

        assert "Invalid API key format" in str(exc.value)

    @pytest.mark.asyncio
    async def test_key_not_found(self):
        """Non-existent keys are rejected."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)

        with pytest.raises(AuthenticationError) as exc:
            await service.validate_api_key("cbk_live_nonexistent12345")

        assert "Invalid API key" in str(exc.value)

    @pytest.mark.asyncio
    async def test_hash_mismatch(self):
        """Keys with wrong hash are rejected."""
        session = AsyncMock()

        # Create a mock API key with a hash for a different key
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = uuid4()
        mock_key.key_hash = PasswordHasher().hash("cbk_live_different_key")
        mock_key.status = "active"
        mock_key.expires_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)

        with pytest.raises(AuthenticationError) as exc:
            await service.validate_api_key("cbk_live_wrongkey12345678")

        assert "Invalid API key" in str(exc.value)

    @pytest.mark.asyncio
    async def test_expired_key(self):
        """Expired keys are rejected and auto-revoked."""
        session = AsyncMock()

        # Generate a real key and its hash
        hasher = PasswordHasher()
        test_key = "cbk_live_testkey1234567890"
        test_hash = hasher.hash(test_key)

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = uuid4()
        mock_key.key_hash = test_hash
        mock_key.key_prefix = test_key[:20]
        mock_key.status = "active"
        mock_key.expires_at = datetime.now(UTC) - timedelta(hours=1)  # Expired

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        service = APIKeyService(session)

        with pytest.raises(AuthenticationError) as exc:
            await service.validate_api_key(test_key)

        assert "API key expired" in str(exc.value)
        assert mock_key.status == "revoked"  # Auto-revoked
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_valid_key_returns_data(self):
        """Valid keys return APIKeyData."""
        session = AsyncMock()

        # Generate a real key and its hash
        hasher = PasswordHasher()
        test_key = "cbk_live_validkey1234567890"
        test_hash = hasher.hash(test_key)
        key_id = uuid4()
        created_at = datetime.now(UTC)

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = key_id
        mock_key.key_hash = test_hash
        mock_key.key_prefix = test_key[:20]
        mock_key.name = "Test Key"
        mock_key.environment = "live"
        mock_key.permissions = ["billing:read", "billing:write"]
        mock_key.status = "active"
        mock_key.created_at = created_at
        mock_key.expires_at = None
        mock_key.last_used_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        service = APIKeyService(session)
        result = await service.validate_api_key(test_key)

        assert isinstance(result, APIKeyData)
        assert result.key_id == key_id
        assert result.name == "Test Key"
        assert result.environment == "live"
        assert result.permissions == ["billing:read", "billing:write"]
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_valid_key_updates_last_used(self):
        """Valid key validation updates last_used_at."""
        session = AsyncMock()

        hasher = PasswordHasher()
        test_key = "cbk_live_updatekey12345678"
        test_hash = hasher.hash(test_key)

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = uuid4()
        mock_key.key_hash = test_hash
        mock_key.key_prefix = test_key[:20]
        mock_key.name = "Test"
        mock_key.environment = "live"
        mock_key.permissions = []
        mock_key.status = "active"
        mock_key.created_at = datetime.now(UTC)
        mock_key.expires_at = None
        mock_key.last_used_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        service = APIKeyService(session)
        await service.validate_api_key(test_key, update_last_used=True)

        assert mock_key.last_used_at is not None
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_valid_key_skip_last_used_update(self):
        """Can skip last_used_at update."""
        session = AsyncMock()

        hasher = PasswordHasher()
        test_key = "cbk_live_skipupdate12345"
        test_hash = hasher.hash(test_key)

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = uuid4()
        mock_key.key_hash = test_hash
        mock_key.key_prefix = test_key[:20]
        mock_key.name = "Test"
        mock_key.environment = "live"
        mock_key.permissions = []
        mock_key.status = "active"
        mock_key.created_at = datetime.now(UTC)
        mock_key.expires_at = None
        mock_key.last_used_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        service = APIKeyService(session)
        await service.validate_api_key(test_key, update_last_used=False)

        # last_used_at should not be set
        assert mock_key.last_used_at is None


class TestAPIKeyServiceRevoke:
    """Tests for API key revocation."""

    @pytest.mark.asyncio
    async def test_revoke_existing_key(self):
        """Can revoke an existing key."""
        session = AsyncMock()
        key_id = uuid4()

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = key_id
        mock_key.name = "Test Key"
        mock_key.status = "active"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_key)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        service = APIKeyService(session)
        await service.revoke_api_key(key_id)

        assert mock_key.status == "revoked"
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key(self):
        """Revoking nonexistent key raises error."""
        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)

        with pytest.raises(ValueError, match="API key not found"):
            await service.revoke_api_key(uuid4())


class TestAPIKeyServiceCreate:
    """Tests for API key creation."""

    @pytest.mark.asyncio
    async def test_create_key_with_defaults(self):
        """Can create key with default permissions."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        created_by = uuid4()

        # Mock refresh to set id and created_at
        async def mock_refresh(obj):
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        session.refresh = mock_refresh

        service = APIKeyService(session)
        result = await service.create_api_key(
            name="Test Key",
            created_by=created_by,
        )

        assert isinstance(result, GeneratedAPIKey)
        assert result.name == "Test Key"
        assert result.environment == "live"
        assert result.permissions == ["billing:read", "billing:write"]
        assert result.plaintext_key.startswith("cbk_live_")
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_key_with_expiration(self):
        """Can create key with expiration."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        async def mock_refresh(obj):
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        session.refresh = mock_refresh

        service = APIKeyService(session)
        result = await service.create_api_key(
            name="Expiring Key",
            created_by=uuid4(),
            expires_in_days=30,
        )

        assert result.expires_at is not None
        # Should be approximately 30 days from now
        delta = result.expires_at - datetime.now(UTC)
        assert 29 <= delta.days <= 30

    @pytest.mark.asyncio
    async def test_create_key_custom_permissions(self):
        """Can create key with custom permissions."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        async def mock_refresh(obj):
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        session.refresh = mock_refresh

        service = APIKeyService(session)
        result = await service.create_api_key(
            name="Read Only Key",
            created_by=uuid4(),
            permissions=["billing:read"],
        )

        assert result.permissions == ["billing:read"]

    @pytest.mark.asyncio
    async def test_create_key_test_environment(self):
        """Can create key for test environment."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        async def mock_refresh(obj):
            obj.id = uuid4()
            obj.created_at = datetime.now(UTC)

        session.refresh = mock_refresh

        service = APIKeyService(session)
        result = await service.create_api_key(
            name="Test Environment Key",
            created_by=uuid4(),
            environment="test",
        )

        assert result.environment == "test"
        assert result.plaintext_key.startswith("cbk_test_")


class TestAPIKeyServiceList:
    """Tests for listing API keys."""

    @pytest.mark.asyncio
    async def test_list_returns_active_keys(self):
        """List returns active and rotating keys."""
        session = AsyncMock()

        # Create mock keys with proper attribute access
        key1 = MagicMock()
        key1.id = uuid4()
        key1.name = "Key 1"
        key1.key_prefix = "cbk_live_key1prefix"
        key1.environment = "live"
        key1.permissions = ["billing:read"]
        key1.status = "active"
        key1.created_at = datetime.now(UTC)
        key1.expires_at = None
        key1.last_used_at = None

        key2 = MagicMock()
        key2.id = uuid4()
        key2.name = "Key 2"
        key2.key_prefix = "cbk_test_key2prefix"
        key2.environment = "test"
        key2.permissions = ["billing:write"]
        key2.status = "rotating"
        key2.created_at = datetime.now(UTC)
        key2.expires_at = datetime.now(UTC) + timedelta(days=7)
        key2.last_used_at = datetime.now(UTC)

        mock_keys = [key1, key2]

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=mock_keys))
        )
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)
        result = await service.list_api_keys()

        assert len(result) == 2
        assert all(isinstance(k, APIKeyData) for k in result)
        assert result[0].name == "Key 1"
        assert result[1].name == "Key 2"

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """List returns empty list when no keys."""
        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)
        result = await service.list_api_keys()

        assert result == []


class TestAPIKeyServiceRotate:
    """Tests for API key rotation."""

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_key(self):
        """Rotating nonexistent key raises error."""
        session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)

        service = APIKeyService(session)

        with pytest.raises(ValueError, match="API key not found"):
            await service.rotate_api_key(uuid4())
