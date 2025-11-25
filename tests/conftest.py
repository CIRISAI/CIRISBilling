"""
Pytest Configuration and Fixtures.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AccountIdentity


@pytest.fixture
def test_account_identity() -> AccountIdentity:
    """Create a test account identity."""
    return AccountIdentity(
        oauth_provider="oauth:google",
        external_id="test@example.com",
        wa_id=None,
        tenant_id=None,
    )


@pytest.fixture
def db_session() -> AsyncMock:
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)

    # Default behaviors
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=None)

    # Execute returns a mock result
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=mock_result)

    return session
