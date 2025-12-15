"""
Pytest Configuration and Centralized Fixtures.

Provides high-quality, reusable mocks and fixtures for testing:
- Database sessions and query results
- Account models in various states
- Billing service with mocked dependencies
- API test client with auth overrides
- Auth fixtures (API key, JWT, Google token)
"""

import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Set required environment variables BEFORE importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_billing")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_IDS", "test-client-id,test-android-client-id")
os.environ.setdefault("ADMIN_JWT_SECRET", "test-secret-key-for-jwt-signing-min-32-chars")
os.environ.setdefault("STRIPE_API_KEY", "sk_test_fake_key")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_fake_secret")

from app.api.dependencies import CombinedAuth, UserIdentity
from app.db.models import Account, Charge, Credit
from app.models.api import AccountStatus, ChargeMetadata, TransactionType
from app.models.domain import AccountData, AccountIdentity, ChargeData, CreditData
from app.services.api_key import APIKeyData
from app.services.billing import BillingService

# ============================================================================
# Database Session Fixtures
# ============================================================================


@pytest.fixture
def db_session() -> AsyncMock:
    """Create a mock database session with sensible defaults."""
    session = AsyncMock(spec=AsyncSession)

    # Basic operations
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.merge = AsyncMock()
    session.refresh = AsyncMock()
    session.get = AsyncMock(return_value=None)

    # Default execute returns empty result
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    mock_result.fetchall = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=mock_result)

    return session


@pytest.fixture
def db_session_factory(db_session: AsyncMock):
    """Factory for creating customized db sessions."""

    def _create_session(
        account: Any | None = None,
        charge: Any | None = None,
        credit: Any | None = None,
        accounts_list: list | None = None,
    ) -> AsyncMock:
        """Create a session configured to return specific objects."""
        mock_result = MagicMock()

        # Handle account lookup
        if account is not None:
            mock_result.scalar_one_or_none = MagicMock(return_value=account)
        else:
            mock_result.scalar_one_or_none = MagicMock(return_value=None)

        # Handle list queries
        if accounts_list is not None:
            mock_result.scalars = MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=accounts_list))
            )

        db_session.execute = AsyncMock(return_value=mock_result)

        # Handle get for charge/credit verification
        if charge is not None:
            db_session.get = AsyncMock(side_effect=[charge, account])
        elif credit is not None:
            db_session.get = AsyncMock(side_effect=[credit, account])

        return db_session

    return _create_session


# ============================================================================
# Account Identity Fixtures
# ============================================================================


@pytest.fixture
def test_account_identity() -> AccountIdentity:
    """Standard test account identity."""
    return AccountIdentity(
        oauth_provider="oauth:google",
        external_id="test@example.com",
        wa_id=None,
        tenant_id=None,
    )


@pytest.fixture
def test_account_identity_with_wa() -> AccountIdentity:
    """Test account identity with wa_id and tenant_id."""
    return AccountIdentity(
        oauth_provider="oauth:google",
        external_id="test@example.com",
        wa_id="wa-test-001",
        tenant_id="tenant-acme",
    )


@pytest.fixture
def discord_account_identity() -> AccountIdentity:
    """Discord OAuth account identity."""
    return AccountIdentity(
        oauth_provider="oauth:discord",
        external_id="discord-user-123456",
        wa_id=None,
        tenant_id=None,
    )


# ============================================================================
# Mock Account Fixtures
# ============================================================================


def create_mock_account(
    account_id: UUID | None = None,
    oauth_provider: str = "oauth:google",
    external_id: str = "test@example.com",
    wa_id: str | None = None,
    tenant_id: str | None = None,
    balance_minor: int = 1000,
    currency: str = "USD",
    status: AccountStatus = AccountStatus.ACTIVE,
    plan_name: str = "free",
    paid_credits: int = 100,
    free_uses_remaining: int = 5,
    daily_free_uses_remaining: int = 2,
    daily_free_uses_limit: int = 2,
    total_uses: int = 0,
    customer_email: str | None = None,
    display_name: str | None = None,
) -> MagicMock:
    """Factory function to create mock Account objects."""
    account = MagicMock(spec=Account)
    account.id = account_id or uuid4()
    account.oauth_provider = oauth_provider
    account.external_id = external_id
    account.wa_id = wa_id
    account.tenant_id = tenant_id
    account.balance_minor = balance_minor
    account.currency = currency
    account.status = status
    account.plan_name = plan_name
    account.paid_credits = paid_credits
    account.free_uses_remaining = free_uses_remaining
    account.daily_free_uses_remaining = daily_free_uses_remaining
    account.daily_free_uses_limit = daily_free_uses_limit
    account.daily_free_uses_reset_at = None
    account.total_uses = total_uses
    account.customer_email = customer_email
    account.display_name = display_name
    account.marketing_opt_in = False
    account.marketing_opt_in_at = None
    account.marketing_opt_in_source = None
    account.user_role = None
    account.agent_id = None
    account.created_at = datetime.now(UTC)
    account.updated_at = datetime.now(UTC)
    return account


@pytest.fixture
def active_account() -> MagicMock:
    """Active account with credits."""
    return create_mock_account(
        balance_minor=1000,
        paid_credits=100,
        free_uses_remaining=5,
        daily_free_uses_remaining=2,
    )


@pytest.fixture
def active_account_no_credits() -> MagicMock:
    """Active account with zero credits."""
    return create_mock_account(
        balance_minor=0,
        paid_credits=0,
        free_uses_remaining=0,
        daily_free_uses_remaining=0,
    )


@pytest.fixture
def suspended_account() -> MagicMock:
    """Suspended account."""
    return create_mock_account(
        status=AccountStatus.SUSPENDED,
        balance_minor=500,
        paid_credits=50,
    )


@pytest.fixture
def closed_account() -> MagicMock:
    """Closed account."""
    return create_mock_account(
        status=AccountStatus.CLOSED,
        balance_minor=0,
        paid_credits=0,
    )


@pytest.fixture
def account_with_daily_free() -> MagicMock:
    """Account with only daily free uses remaining."""
    return create_mock_account(
        balance_minor=0,
        paid_credits=0,
        free_uses_remaining=0,
        daily_free_uses_remaining=2,
    )


@pytest.fixture
def account_with_free_uses() -> MagicMock:
    """Account with only one-time free uses remaining."""
    return create_mock_account(
        balance_minor=0,
        paid_credits=0,
        free_uses_remaining=5,
        daily_free_uses_remaining=0,
    )


# ============================================================================
# Mock Charge/Credit Fixtures
# ============================================================================


def create_mock_charge(
    charge_id: UUID | None = None,
    account_id: UUID | None = None,
    amount_minor: int = 100,
    currency: str = "USD",
    balance_before: int = 1000,
    balance_after: int = 900,
    description: str = "Test charge",
    idempotency_key: str | None = None,
) -> MagicMock:
    """Factory function to create mock Charge objects."""
    charge = MagicMock(spec=Charge)
    charge.id = charge_id or uuid4()
    charge.account_id = account_id or uuid4()
    charge.amount_minor = amount_minor
    charge.currency = currency
    charge.balance_before = balance_before
    charge.balance_after = balance_after
    charge.description = description
    charge.idempotency_key = idempotency_key
    charge.metadata_message_id = None
    charge.metadata_agent_id = None
    charge.metadata_channel_id = None
    charge.metadata_request_id = None
    charge.created_at = datetime.now(UTC)
    return charge


def create_mock_credit(
    credit_id: UUID | None = None,
    account_id: UUID | None = None,
    amount_minor: int = 500,
    currency: str = "USD",
    balance_before: int = 500,
    balance_after: int = 1000,
    transaction_type: TransactionType = TransactionType.GRANT,
    description: str = "Test credit",
    external_transaction_id: str | None = None,
    idempotency_key: str | None = None,
) -> MagicMock:
    """Factory function to create mock Credit objects."""
    credit = MagicMock(spec=Credit)
    credit.id = credit_id or uuid4()
    credit.account_id = account_id or uuid4()
    credit.amount_minor = amount_minor
    credit.currency = currency
    credit.balance_before = balance_before
    credit.balance_after = balance_after
    credit.transaction_type = transaction_type
    credit.description = description
    credit.external_transaction_id = external_transaction_id
    credit.idempotency_key = idempotency_key
    credit.is_test_purchase = False
    credit.created_at = datetime.now(UTC)
    return credit


@pytest.fixture
def mock_charge(active_account: MagicMock) -> MagicMock:
    """Standard mock charge."""
    return create_mock_charge(account_id=active_account.id)


@pytest.fixture
def mock_credit(active_account: MagicMock) -> MagicMock:
    """Standard mock credit."""
    return create_mock_credit(account_id=active_account.id)


# ============================================================================
# Billing Service Fixtures
# ============================================================================


@pytest.fixture
def billing_service(db_session: AsyncMock) -> BillingService:
    """BillingService with mocked database session."""
    return BillingService(db_session)


@pytest.fixture
def billing_service_with_account(
    db_session: AsyncMock, active_account: MagicMock
) -> BillingService:
    """BillingService configured to return an active account."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=active_account)
    db_session.execute = AsyncMock(return_value=mock_result)
    return BillingService(db_session)


# ============================================================================
# Auth Fixtures
# ============================================================================


@pytest.fixture
def api_key_auth() -> CombinedAuth:
    """API key authentication context."""
    api_key = APIKeyData(
        key_id=uuid4(),
        name="Test API Key",
        key_prefix="cbk_test_prefix123",
        environment="test",
        permissions=["billing:read", "billing:write"],
        status="active",
        created_at=datetime.now(UTC),
        expires_at=None,
        last_used_at=None,
    )
    return CombinedAuth(auth_type="api_key", api_key=api_key, user=None)


@pytest.fixture
def api_key_auth_read_only() -> CombinedAuth:
    """API key authentication with read-only permissions."""
    api_key = APIKeyData(
        key_id=uuid4(),
        name="Read Only Key",
        key_prefix="cbk_test_readonly1",
        environment="test",
        permissions=["billing:read"],
        status="active",
        created_at=datetime.now(UTC),
        expires_at=None,
        last_used_at=None,
    )
    return CombinedAuth(auth_type="api_key", api_key=api_key, user=None)


@pytest.fixture
def jwt_auth() -> CombinedAuth:
    """JWT authentication context."""
    user = UserIdentity(
        oauth_provider="oauth:google",
        external_id="user@example.com",
        email="user@example.com",
        name="Test User",
    )
    return CombinedAuth(auth_type="jwt", api_key=None, user=user)


@pytest.fixture
def user_identity() -> UserIdentity:
    """Standard user identity from JWT."""
    return UserIdentity(
        oauth_provider="oauth:google",
        external_id="user@example.com",
        email="user@example.com",
        name="Test User",
    )


# ============================================================================
# FastAPI Test Client Fixtures
# ============================================================================


@pytest.fixture
def app() -> FastAPI:
    """Create FastAPI app for testing."""
    from app.main import app as main_app

    return main_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Synchronous test client."""
    return TestClient(app)


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Async test client for async tests."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
def mock_db_dependency(db_session: AsyncMock):
    """Override database dependencies for testing."""
    from app.db.session import get_read_db, get_write_db

    async def override_get_write_db():
        yield db_session

    async def override_get_read_db():
        yield db_session

    return {
        get_write_db: override_get_write_db,
        get_read_db: override_get_read_db,
    }


@pytest.fixture
def mock_auth_dependency(api_key_auth: CombinedAuth):
    """Override auth dependency for testing."""
    from app.api.dependencies import get_api_key_or_jwt

    async def override_auth():
        return api_key_auth

    return {get_api_key_or_jwt: override_auth}


@pytest.fixture
def mock_user_auth_dependency(jwt_auth: CombinedAuth):
    """Override auth dependency with JWT user auth."""
    from app.api.dependencies import get_api_key_or_jwt

    async def override_auth():
        return jwt_auth

    return {get_api_key_or_jwt: override_auth}


@pytest.fixture
def authenticated_client(
    app: FastAPI, mock_db_dependency: dict, mock_auth_dependency: dict, active_account: MagicMock
) -> TestClient:
    """Test client with mocked auth and database returning active account."""
    from app.db.session import get_read_db, get_write_db

    # Configure db to return the active account
    async def get_configured_db():
        from unittest.mock import AsyncMock, MagicMock

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=active_account)
        mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        session.execute = AsyncMock(return_value=mock_result)
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        session.get = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides.update(mock_auth_dependency)
    app.dependency_overrides[get_write_db] = get_configured_db
    app.dependency_overrides[get_read_db] = get_configured_db

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


# ============================================================================
# Domain Model Fixtures
# ============================================================================


@pytest.fixture
def account_data(active_account: MagicMock) -> AccountData:
    """AccountData domain object."""
    return AccountData(
        account_id=active_account.id,
        oauth_provider=active_account.oauth_provider,
        external_id=active_account.external_id,
        wa_id=active_account.wa_id,
        tenant_id=active_account.tenant_id,
        customer_email=active_account.customer_email,
        balance_minor=active_account.balance_minor,
        currency=active_account.currency,
        plan_name=active_account.plan_name,
        status=active_account.status,
        paid_credits=active_account.paid_credits,
        marketing_opt_in=active_account.marketing_opt_in,
        marketing_opt_in_at=active_account.marketing_opt_in_at,
        marketing_opt_in_source=active_account.marketing_opt_in_source,
        created_at=active_account.created_at,
        updated_at=active_account.updated_at,
        free_uses_remaining=active_account.free_uses_remaining,
        daily_free_uses_remaining=active_account.daily_free_uses_remaining,
        daily_free_uses_limit=active_account.daily_free_uses_limit,
        daily_free_uses_reset_at=active_account.daily_free_uses_reset_at,
    )


@pytest.fixture
def charge_data(mock_charge: MagicMock) -> ChargeData:
    """ChargeData domain object."""
    return ChargeData(
        charge_id=mock_charge.id,
        account_id=mock_charge.account_id,
        amount_minor=mock_charge.amount_minor,
        currency=mock_charge.currency,
        balance_before=mock_charge.balance_before,
        balance_after=mock_charge.balance_after,
        description=mock_charge.description,
        metadata=ChargeMetadata(),
        created_at=mock_charge.created_at,
    )


@pytest.fixture
def credit_data(mock_credit: MagicMock) -> CreditData:
    """CreditData domain object."""
    return CreditData(
        credit_id=mock_credit.id,
        account_id=mock_credit.account_id,
        amount_minor=mock_credit.amount_minor,
        currency=mock_credit.currency,
        balance_before=mock_credit.balance_before,
        balance_after=mock_credit.balance_after,
        transaction_type=mock_credit.transaction_type,
        description=mock_credit.description,
        external_transaction_id=mock_credit.external_transaction_id,
        created_at=mock_credit.created_at,
    )


# ============================================================================
# Request Body Fixtures
# ============================================================================


@pytest.fixture
def credit_check_request_body() -> dict:
    """Standard credit check request body."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "test@example.com",
        "context": {},
    }


@pytest.fixture
def create_charge_request_body() -> dict:
    """Standard create charge request body."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "test@example.com",
        "amount_minor": 100,
        "currency": "USD",
        "description": "Test charge",
        "metadata": {},
    }


@pytest.fixture
def add_credits_request_body() -> dict:
    """Standard add credits request body."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "test@example.com",
        "amount_minor": 500,
        "currency": "USD",
        "description": "Test credit",
        "transaction_type": "grant",
    }


@pytest.fixture
def create_account_request_body() -> dict:
    """Standard create account request body."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "newuser@example.com",
        "initial_balance_minor": 0,
        "currency": "USD",
        "plan_name": "free",
    }


# ============================================================================
# Utility Fixtures
# ============================================================================


@pytest.fixture
def fixed_uuid() -> UUID:
    """Fixed UUID for deterministic testing."""
    return UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def fixed_datetime() -> datetime:
    """Fixed datetime for deterministic testing."""
    return datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_token_revocation_cache():
    """Reset token revocation cache before each test."""
    from app.services.token_revocation import TokenRevocationService

    TokenRevocationService._cache = {}
    TokenRevocationService._cache_loaded = False
    TokenRevocationService._last_cleanup = 0
    yield
    TokenRevocationService._cache = {}
    TokenRevocationService._cache_loaded = False
    TokenRevocationService._last_cleanup = 0
