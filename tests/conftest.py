"""
Pytest Configuration and Fixtures.
"""

import asyncio
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.models import Base
from app.models.domain import AccountIdentity


# Test database URL
TEST_DATABASE_URL = "postgresql+asyncpg://billing_admin:password@localhost:5432/ciris_billing_test"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    """Create test database engine."""
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )

    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine

    # Cleanup
    await test_engine.dispose()


@pytest.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        try:
            yield session
            await session.rollback()  # Rollback after each test
        finally:
            await session.close()


@pytest.fixture
def test_account_identity() -> AccountIdentity:
    """Create a test account identity."""
    return AccountIdentity(
        oauth_provider="oauth:google",
        external_id="test@example.com",
        wa_id=None,
        tenant_id=None,
    )
