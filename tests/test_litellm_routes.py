"""
Tests for LiteLLM proxy integration endpoints.
"""

import pytest
from pydantic import ValidationError

from app.models.api import (
    LiteLLMAuthRequest,
    LiteLLMAuthResponse,
    LiteLLMChargeRequest,
    LiteLLMChargeResponse,
    LiteLLMUsageLogRequest,
    LiteLLMUsageLogResponse,
)


class TestLiteLLMAuthRequest:
    """Tests for LiteLLMAuthRequest model."""

    def test_valid_request(self):
        """Test valid auth request."""
        request = LiteLLMAuthRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
            model="groq/llama-3.1-70b",
            interaction_id="int-456",
        )
        assert request.oauth_provider == "oauth:google"
        assert request.external_id == "user-123"
        assert request.model == "groq/llama-3.1-70b"
        assert request.interaction_id == "int-456"

    def test_minimal_request(self):
        """Test minimal auth request with only required fields."""
        request = LiteLLMAuthRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
        )
        assert request.model is None
        assert request.interaction_id is None

    def test_invalid_oauth_provider(self):
        """Test that oauth_provider must start with 'oauth:'."""
        with pytest.raises(ValidationError) as exc_info:
            LiteLLMAuthRequest(
                oauth_provider="google",  # Missing oauth: prefix
                external_id="user-123",
            )
        assert "oauth_provider must start with" in str(exc_info.value)


class TestLiteLLMAuthResponse:
    """Tests for LiteLLMAuthResponse model."""

    def test_authorized_response(self):
        """Test authorized response."""
        response = LiteLLMAuthResponse(
            authorized=True,
            credits_remaining=10,
            interaction_id="int-456",
        )
        assert response.authorized is True
        assert response.credits_remaining == 10
        assert response.reason is None
        assert response.interaction_id == "int-456"

    def test_unauthorized_response(self):
        """Test unauthorized response with reason."""
        response = LiteLLMAuthResponse(
            authorized=False,
            credits_remaining=0,
            reason="Insufficient credits",
        )
        assert response.authorized is False
        assert response.reason == "Insufficient credits"


class TestLiteLLMChargeRequest:
    """Tests for LiteLLMChargeRequest model."""

    def test_valid_request(self):
        """Test valid charge request."""
        request = LiteLLMChargeRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
            interaction_id="int-456",
        )
        assert request.oauth_provider == "oauth:google"
        assert request.interaction_id == "int-456"
        assert request.idempotency_key is None

    def test_with_idempotency_key(self):
        """Test charge request with idempotency key."""
        request = LiteLLMChargeRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
            interaction_id="int-456",
            idempotency_key="custom-key-789",
        )
        assert request.idempotency_key == "custom-key-789"


class TestLiteLLMChargeResponse:
    """Tests for LiteLLMChargeResponse model."""

    def test_successful_charge(self):
        """Test successful charge response."""
        from uuid import uuid4

        charge_id = uuid4()
        response = LiteLLMChargeResponse(
            charged=True,
            credits_deducted=1,
            credits_remaining=9,
            charge_id=charge_id,
        )
        assert response.charged is True
        assert response.credits_deducted == 1
        assert response.credits_remaining == 9
        assert response.charge_id == charge_id

    def test_failed_charge(self):
        """Test failed charge response."""
        response = LiteLLMChargeResponse(
            charged=False,
            credits_deducted=0,
            credits_remaining=0,
            charge_id=None,
        )
        assert response.charged is False
        assert response.credits_deducted == 0


class TestLiteLLMUsageLogRequest:
    """Tests for LiteLLMUsageLogRequest model."""

    def test_valid_request(self):
        """Test valid usage log request."""
        request = LiteLLMUsageLogRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
            interaction_id="int-456",
            total_llm_calls=45,
            total_prompt_tokens=80000,
            total_completion_tokens=5000,
            models_used=["groq/llama-3.1-70b", "together/mixtral"],
            actual_cost_cents=12,
            duration_ms=8500,
        )
        assert request.total_llm_calls == 45
        assert request.total_prompt_tokens == 80000
        assert request.total_completion_tokens == 5000
        assert len(request.models_used) == 2
        assert request.actual_cost_cents == 12
        assert request.duration_ms == 8500
        assert request.error_count == 0
        assert request.fallback_count == 0

    def test_with_errors(self):
        """Test usage log with error tracking."""
        request = LiteLLMUsageLogRequest(
            oauth_provider="oauth:google",
            external_id="user-123",
            interaction_id="int-456",
            total_llm_calls=50,
            total_prompt_tokens=90000,
            total_completion_tokens=6000,
            models_used=["groq/llama-3.1-70b"],
            actual_cost_cents=15,
            duration_ms=12000,
            error_count=3,
            fallback_count=2,
        )
        assert request.error_count == 3
        assert request.fallback_count == 2

    def test_min_llm_calls(self):
        """Test that total_llm_calls must be at least 1."""
        with pytest.raises(ValidationError):
            LiteLLMUsageLogRequest(
                oauth_provider="oauth:google",
                external_id="user-123",
                interaction_id="int-456",
                total_llm_calls=0,  # Must be >= 1
                total_prompt_tokens=1000,
                total_completion_tokens=100,
                models_used=[],
                actual_cost_cents=1,
                duration_ms=100,
            )


class TestLiteLLMUsageLogResponse:
    """Tests for LiteLLMUsageLogResponse model."""

    def test_successful_log(self):
        """Test successful log response."""
        from uuid import uuid4

        log_id = uuid4()
        response = LiteLLMUsageLogResponse(
            logged=True,
            usage_log_id=log_id,
        )
        assert response.logged is True
        assert response.usage_log_id == log_id
