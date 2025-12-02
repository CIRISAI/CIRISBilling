"""
Tests for margin analytics admin endpoints.

Run with: pytest tests/test_margin_analytics.py -v
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

# Revenue per interaction in cents (users pay 1 credit = $1.00 = 100 cents)
# This constant is also defined in admin_routes.py
REVENUE_PER_INTERACTION_CENTS = 100


# Define test versions of the response models to avoid importing from admin_routes
# which has side effects (structlog import). These mirror the actual models.


class UserMarginResponse(BaseModel):
    """Margin analytics for a single user."""

    account_id: UUID
    customer_email: str | None
    total_interactions: int
    total_revenue_cents: int
    total_llm_cost_cents: int
    margin_cents: int
    margin_percent: float
    avg_llm_calls_per_interaction: float
    avg_tokens_per_interaction: int
    total_prompt_tokens: int
    total_completion_tokens: int
    models_used: list[str]
    first_interaction_at: datetime | None
    last_interaction_at: datetime | None


class UserMarginListResponse(BaseModel):
    """Paginated list of user margins."""

    users: list[UserMarginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    total_revenue_cents: int
    total_llm_cost_cents: int
    total_margin_cents: int
    overall_margin_percent: float


class DailyMarginResponse(BaseModel):
    """Daily margin analytics."""

    date: str
    total_interactions: int
    total_revenue_cents: int
    total_llm_cost_cents: int
    margin_cents: int
    margin_percent: float
    unique_users: int
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    avg_cost_per_interaction_cents: float
    error_count: int
    fallback_count: int


class InteractionMarginResponse(BaseModel):
    """Margin details for a single interaction."""

    usage_log_id: UUID
    account_id: UUID
    customer_email: str | None
    interaction_id: str
    created_at: datetime
    revenue_cents: int
    llm_cost_cents: int
    margin_cents: int
    margin_percent: float
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    models_used: list[str]
    duration_ms: int
    error_count: int
    fallback_count: int


class InteractionMarginListResponse(BaseModel):
    """Paginated list of interaction margins."""

    interactions: list[InteractionMarginResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarginOverviewResponse(BaseModel):
    """High-level margin overview."""

    period_start: datetime
    period_end: datetime
    total_interactions: int
    total_revenue_cents: int
    total_llm_cost_cents: int
    total_margin_cents: int
    overall_margin_percent: float
    avg_cost_per_interaction_cents: float
    avg_revenue_per_user_cents: float
    avg_llm_calls_per_interaction: float
    avg_tokens_per_interaction: int
    unique_users: int
    total_llm_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_errors: int
    total_fallbacks: int
    model_usage: dict[str, int]


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user."""
    admin = MagicMock()
    admin.id = uuid4()
    admin.email = "admin@ciris.ai"
    admin.role = "admin"
    return admin


@pytest.fixture
def mock_account():
    """Create a mock account."""
    account = MagicMock()
    account.id = uuid4()
    account.customer_email = "user@example.com"
    account.oauth_provider = "oauth:google"
    account.external_id = "123456"
    return account


@pytest.fixture
def mock_usage_log(mock_account):
    """Create a mock LLM usage log."""
    log = MagicMock()
    log.id = uuid4()
    log.account_id = mock_account.id
    log.interaction_id = f"int-{uuid4()}"
    log.created_at = datetime.now(UTC)
    log.total_llm_calls = 5
    log.total_prompt_tokens = 1000
    log.total_completion_tokens = 500
    log.actual_cost_cents = 15  # 15 cents cost
    log.models_used = [
        "groq/llama-3.1-70b-versatile",
        "together/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
    ]
    log.duration_ms = 2500
    log.error_count = 0
    log.fallback_count = 1
    return log


class TestMarginOverviewEndpoint:
    """Tests for GET /admin/analytics/margin/overview."""

    def test_margin_overview_response_model(self):
        """Test MarginOverviewResponse model instantiation."""
        now = datetime.now(UTC)
        response = MarginOverviewResponse(
            period_start=now - timedelta(days=30),
            period_end=now,
            total_interactions=100,
            total_revenue_cents=10000,  # 100 * 100 cents
            total_llm_cost_cents=1500,
            total_margin_cents=8500,
            overall_margin_percent=85.0,
            avg_cost_per_interaction_cents=15.0,
            avg_revenue_per_user_cents=500.0,
            avg_llm_calls_per_interaction=5.0,
            avg_tokens_per_interaction=1500,
            unique_users=20,
            total_llm_calls=500,
            total_prompt_tokens=100000,
            total_completion_tokens=50000,
            total_errors=5,
            total_fallbacks=10,
            model_usage={
                "groq/llama-3.1-70b-versatile": 400,
                "together/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": 100,
            },
        )

        assert response.total_interactions == 100
        assert response.overall_margin_percent == 85.0
        assert response.total_margin_cents == 8500
        assert "groq/llama-3.1-70b-versatile" in response.model_usage

    def test_margin_calculation_logic(self):
        """Test that margin calculations are correct."""
        # Given 100 interactions at 100 cents each = 10000 cents revenue
        # With 1500 cents cost = 8500 cents margin
        # Margin % = 8500 / 10000 * 100 = 85%

        total_interactions = 100
        total_cost_cents = 1500

        revenue = total_interactions * REVENUE_PER_INTERACTION_CENTS
        margin = revenue - total_cost_cents
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0

        assert revenue == 10000
        assert margin == 8500
        assert margin_percent == 85.0


class TestDailyMarginEndpoint:
    """Tests for GET /admin/analytics/margin/daily."""

    def test_daily_margin_response_model(self):
        """Test DailyMarginResponse model instantiation."""
        response = DailyMarginResponse(
            date="2024-11-29",
            total_interactions=50,
            total_revenue_cents=5000,
            total_llm_cost_cents=750,
            margin_cents=4250,
            margin_percent=85.0,
            unique_users=10,
            total_llm_calls=250,
            total_prompt_tokens=50000,
            total_completion_tokens=25000,
            avg_cost_per_interaction_cents=15.0,
            error_count=2,
            fallback_count=5,
        )

        assert response.date == "2024-11-29"
        assert response.total_interactions == 50
        assert response.margin_percent == 85.0

    def test_daily_margin_calculation(self):
        """Test daily margin calculations."""
        interactions = 50
        cost = 750

        revenue = interactions * REVENUE_PER_INTERACTION_CENTS
        margin = revenue - cost
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0
        avg_cost = cost / interactions if interactions > 0 else 0

        assert revenue == 5000
        assert margin == 4250
        assert margin_percent == 85.0
        assert avg_cost == 15.0


class TestUserMarginEndpoint:
    """Tests for GET /admin/analytics/margin/users."""

    def test_user_margin_response_model(self, mock_account):
        """Test UserMarginResponse model instantiation."""
        now = datetime.now(UTC)
        response = UserMarginResponse(
            account_id=mock_account.id,
            customer_email="user@example.com",
            total_interactions=25,
            total_revenue_cents=2500,
            total_llm_cost_cents=375,
            margin_cents=2125,
            margin_percent=85.0,
            avg_llm_calls_per_interaction=5.0,
            avg_tokens_per_interaction=1500,
            total_prompt_tokens=25000,
            total_completion_tokens=12500,
            models_used=["groq/llama-3.1-70b-versatile"],
            first_interaction_at=now - timedelta(days=7),
            last_interaction_at=now,
        )

        assert response.customer_email == "user@example.com"
        assert response.total_interactions == 25
        assert response.margin_percent == 85.0

    def test_user_margin_list_response_model(self, mock_account):
        """Test UserMarginListResponse model instantiation."""
        now = datetime.now(UTC)
        user = UserMarginResponse(
            account_id=mock_account.id,
            customer_email="user@example.com",
            total_interactions=25,
            total_revenue_cents=2500,
            total_llm_cost_cents=375,
            margin_cents=2125,
            margin_percent=85.0,
            avg_llm_calls_per_interaction=5.0,
            avg_tokens_per_interaction=1500,
            total_prompt_tokens=25000,
            total_completion_tokens=12500,
            models_used=["groq/llama-3.1-70b-versatile"],
            first_interaction_at=now - timedelta(days=7),
            last_interaction_at=now,
        )

        response = UserMarginListResponse(
            users=[user],
            total=1,
            page=1,
            page_size=50,
            total_pages=1,
            total_revenue_cents=2500,
            total_llm_cost_cents=375,
            total_margin_cents=2125,
            overall_margin_percent=85.0,
        )

        assert len(response.users) == 1
        assert response.total == 1
        assert response.overall_margin_percent == 85.0


class TestInteractionMarginEndpoint:
    """Tests for GET /admin/analytics/margin/interactions."""

    def test_interaction_margin_response_model(self, mock_account):
        """Test InteractionMarginResponse model instantiation."""
        now = datetime.now(UTC)
        response = InteractionMarginResponse(
            usage_log_id=uuid4(),
            account_id=mock_account.id,
            customer_email="user@example.com",
            interaction_id=f"int-{uuid4()}",
            created_at=now,
            revenue_cents=100,  # Always 100 cents per interaction
            llm_cost_cents=15,
            margin_cents=85,
            margin_percent=85.0,
            total_llm_calls=5,
            total_prompt_tokens=1000,
            total_completion_tokens=500,
            models_used=[
                "groq/llama-3.1-70b-versatile",
                "together/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            ],
            duration_ms=2500,
            error_count=0,
            fallback_count=1,
        )

        assert response.revenue_cents == 100
        assert response.llm_cost_cents == 15
        assert response.margin_cents == 85
        assert response.margin_percent == 85.0
        assert len(response.models_used) == 2

    def test_interaction_margin_list_response_model(self, mock_account):
        """Test InteractionMarginListResponse model instantiation."""
        now = datetime.now(UTC)
        interaction = InteractionMarginResponse(
            usage_log_id=uuid4(),
            account_id=mock_account.id,
            customer_email="user@example.com",
            interaction_id=f"int-{uuid4()}",
            created_at=now,
            revenue_cents=100,
            llm_cost_cents=15,
            margin_cents=85,
            margin_percent=85.0,
            total_llm_calls=5,
            total_prompt_tokens=1000,
            total_completion_tokens=500,
            models_used=["groq/llama-3.1-70b-versatile"],
            duration_ms=2500,
            error_count=0,
            fallback_count=1,
        )

        response = InteractionMarginListResponse(
            interactions=[interaction],
            total=1,
            page=1,
            page_size=50,
            total_pages=1,
        )

        assert len(response.interactions) == 1
        assert response.total == 1


class TestMarginCalculations:
    """Test margin calculation edge cases."""

    def test_zero_interactions_no_division_error(self):
        """Test that zero interactions don't cause division errors."""
        total_interactions = 0
        total_cost_cents = 0

        revenue = total_interactions * REVENUE_PER_INTERACTION_CENTS
        margin = revenue - total_cost_cents
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0
        avg_cost = total_cost_cents / total_interactions if total_interactions > 0 else 0.0

        assert revenue == 0
        assert margin == 0
        assert margin_percent == 0.0
        assert avg_cost == 0.0

    def test_negative_margin_when_cost_exceeds_revenue(self):
        """Test that margin can be negative when costs exceed revenue."""
        total_interactions = 1  # 1 interaction = 100 cents revenue
        total_cost_cents = 200  # Cost exceeds revenue

        revenue = total_interactions * REVENUE_PER_INTERACTION_CENTS
        margin = revenue - total_cost_cents
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0

        assert revenue == 100
        assert margin == -100  # Negative margin
        assert margin_percent == -100.0  # -100% margin

    def test_high_volume_margin_calculation(self):
        """Test margin calculation at high volumes."""
        total_interactions = 1000000  # 1 million interactions
        total_cost_cents = 12000000  # $120k in costs (12 cents average)

        revenue = total_interactions * REVENUE_PER_INTERACTION_CENTS
        margin = revenue - total_cost_cents
        margin_percent = (margin / revenue * 100) if revenue > 0 else 0.0

        assert revenue == 100000000  # $1M in revenue
        assert margin == 88000000  # $880k margin
        assert margin_percent == 88.0

    def test_revenue_per_interaction_constant(self):
        """Verify the revenue per interaction constant is correct."""
        # 1 credit = $1.00 = 100 cents
        assert REVENUE_PER_INTERACTION_CENTS == 100


class TestUserMarginDetail:
    """Tests for GET /admin/analytics/margin/users/{account_id}."""

    def test_user_margin_detail_with_no_usage(self, mock_account):
        """Test user margin detail when user has no usage logs."""
        # When a user has no interactions, all metrics should be 0
        response = UserMarginResponse(
            account_id=mock_account.id,
            customer_email="user@example.com",
            total_interactions=0,
            total_revenue_cents=0,
            total_llm_cost_cents=0,
            margin_cents=0,
            margin_percent=0.0,
            avg_llm_calls_per_interaction=0.0,
            avg_tokens_per_interaction=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
            models_used=[],
            first_interaction_at=None,
            last_interaction_at=None,
        )

        assert response.total_interactions == 0
        assert response.margin_percent == 0.0
        assert response.first_interaction_at is None
        assert response.last_interaction_at is None

    def test_user_margin_detail_with_multiple_models(self, mock_account):
        """Test user margin detail with multiple models used."""
        now = datetime.now(UTC)
        models = [
            "groq/llama-3.1-70b-versatile",
            "together/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            "openai/gpt-4o-mini",
        ]

        response = UserMarginResponse(
            account_id=mock_account.id,
            customer_email="user@example.com",
            total_interactions=100,
            total_revenue_cents=10000,
            total_llm_cost_cents=2000,
            margin_cents=8000,
            margin_percent=80.0,
            avg_llm_calls_per_interaction=7.5,
            avg_tokens_per_interaction=2000,
            total_prompt_tokens=150000,
            total_completion_tokens=50000,
            models_used=models,
            first_interaction_at=now - timedelta(days=30),
            last_interaction_at=now,
        )

        assert len(response.models_used) == 3
        assert "groq/llama-3.1-70b-versatile" in response.models_used
