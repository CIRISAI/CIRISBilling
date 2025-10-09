"""
End-to-End API Tests

Tests all major API endpoints against local stack.
Run with: pytest tests/e2e/test_api_endpoints.py -v
"""

import time
from uuid import uuid4

import httpx
import pytest

BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    """HTTP client for API requests."""
    return httpx.Client(base_url=BASE_URL, timeout=10.0)


@pytest.fixture
def test_user_1():
    """Test user 1 - has balance."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "test-user-1@example.com",
        "wa_id": "wa-test-001",
        "tenant_id": "tenant-acme",
    }


@pytest.fixture
def test_user_2():
    """Test user 2 - low balance."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "test-user-2@example.com",
        "wa_id": "wa-test-002",
        "tenant_id": "tenant-acme",
    }


@pytest.fixture
def test_user_suspended():
    """Test user - suspended account."""
    return {
        "oauth_provider": "oauth:google",
        "external_id": "suspended-user@example.com",
        "wa_id": "wa-test-003",
        "tenant_id": "tenant-acme",
    }


class TestHealthAndMetrics:
    """Test basic health and metrics endpoints."""

    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    def test_root_endpoint(self, client):
        """Test root endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "version" in data

    def test_metrics_endpoint(self, client):
        """Test Prometheus metrics endpoint."""
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "billing_http_requests_total" in response.text


class TestAccountOperations:
    """Test account CRUD operations."""

    def test_get_existing_account(self, client, test_user_1):
        """Test retrieving an existing account."""
        response = client.get(
            f"/v1/billing/accounts/{test_user_1['oauth_provider']}/{test_user_1['external_id']}",
            params={
                "wa_id": test_user_1["wa_id"],
                "tenant_id": test_user_1["tenant_id"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["oauth_provider"] == test_user_1["oauth_provider"]
        assert data["external_id"] == test_user_1["external_id"]
        assert data["status"] == "active"
        assert data["balance_minor"] > 0

    def test_get_account_not_found(self, client):
        """Test retrieving a non-existent account."""
        response = client.get("/v1/billing/accounts/oauth:google/nonexistent@example.com")
        assert response.status_code == 404

    def test_create_new_account(self, client):
        """Test creating a new account."""
        new_user = f"test-{uuid4()}@example.com"
        response = client.post(
            "/v1/billing/accounts",
            json={
                "oauth_provider": "oauth:google",
                "external_id": new_user,
                "initial_balance_minor": 5000,
                "currency": "USD",
                "plan_name": "test",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["external_id"] == new_user
        assert data["balance_minor"] == 5000
        assert data["status"] == "active"

    def test_upsert_existing_account(self, client, test_user_1):
        """Test upserting an existing account (should return existing)."""
        response = client.post(
            "/v1/billing/accounts",
            json={
                **test_user_1,
                "initial_balance_minor": 9999,  # Should be ignored
                "currency": "USD",
                "plan_name": "pro",
            },
        )
        assert response.status_code == 201
        data = response.json()
        # Should return existing account, not create new
        assert data["external_id"] == test_user_1["external_id"]


class TestCreditChecks:
    """Test credit check operations."""

    def test_credit_check_sufficient_balance(self, client, test_user_1):
        """Test credit check for account with sufficient balance."""
        response = client.post(
            "/v1/billing/credits/check",
            json={**test_user_1, "context": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_credit"] is True
        assert data["credits_remaining"] > 0
        assert data["plan_name"] is not None
        assert data["reason"] is None

    def test_credit_check_zero_balance(self, client):
        """Test credit check for account with zero balance."""
        response = client.post(
            "/v1/billing/credits/check",
            json={
                "oauth_provider": "oauth:discord",
                "external_id": "discord-user-123456",
                "context": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_credit"] is False
        assert data["credits_remaining"] == 0
        assert data["reason"] is not None

    def test_credit_check_suspended_account(self, client, test_user_suspended):
        """Test credit check for suspended account."""
        response = client.post(
            "/v1/billing/credits/check",
            json={**test_user_suspended, "context": {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_credit"] is False
        assert data["reason"] is not None
        assert "suspended" in data["reason"].lower()

    def test_credit_check_nonexistent_account(self, client):
        """Test credit check for non-existent account."""
        response = client.post(
            "/v1/billing/credits/check",
            json={
                "oauth_provider": "oauth:google",
                "external_id": "nonexistent@example.com",
                "context": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_credit"] is False
        assert data["reason"] == "Account not found"


class TestChargeOperations:
    """Test charge creation operations."""

    def test_create_charge_success(self, client, test_user_1):
        """Test successful charge creation."""
        idempotency_key = f"test-charge-{time.time()}"
        response = client.post(
            "/v1/billing/charges",
            json={
                **test_user_1,
                "amount_minor": 100,
                "currency": "USD",
                "description": "E2E test charge",
                "idempotency_key": idempotency_key,
                "metadata": {
                    "message_id": "test-msg",
                    "agent_id": "test-agent",
                    "request_id": "test-req",
                },
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount_minor"] == 100
        assert data["currency"] == "USD"
        assert "charge_id" in data
        assert "balance_after" in data

    def test_create_charge_idempotency(self, client, test_user_1):
        """Test charge idempotency - duplicate should return 409."""
        idempotency_key = f"test-charge-{time.time()}"

        # First charge
        response1 = client.post(
            "/v1/billing/charges",
            json={
                **test_user_1,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge",
                "idempotency_key": idempotency_key,
                "metadata": {},
            },
        )
        assert response1.status_code == 201

        # Duplicate charge
        response2 = client.post(
            "/v1/billing/charges",
            json={
                **test_user_1,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge",
                "idempotency_key": idempotency_key,
                "metadata": {},
            },
        )
        assert response2.status_code == 409

    def test_create_charge_insufficient_balance(self, client, test_user_2):
        """Test charge with insufficient balance."""
        response = client.post(
            "/v1/billing/charges",
            json={
                **test_user_2,
                "amount_minor": 100000,  # More than balance
                "currency": "USD",
                "description": "Test charge",
                "metadata": {},
            },
        )
        assert response.status_code == 402
        assert "insufficient" in response.json()["detail"].lower()

    def test_create_charge_account_not_found(self, client):
        """Test charge for non-existent account."""
        response = client.post(
            "/v1/billing/charges",
            json={
                "oauth_provider": "oauth:google",
                "external_id": "nonexistent@example.com",
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge",
                "metadata": {},
            },
        )
        assert response.status_code == 404

    def test_create_charge_suspended_account(self, client, test_user_suspended):
        """Test charge for suspended account."""
        response = client.post(
            "/v1/billing/charges",
            json={
                **test_user_suspended,
                "amount_minor": 100,
                "currency": "USD",
                "description": "Test charge",
                "metadata": {},
            },
        )
        assert response.status_code == 403


class TestCreditAddition:
    """Test credit addition operations."""

    def test_add_credits_success(self, client):
        """Test successful credit addition."""
        idempotency_key = f"test-credit-{time.time()}"
        response = client.post(
            "/v1/billing/credits",
            json={
                "oauth_provider": "oauth:discord",
                "external_id": "discord-user-123456",
                "amount_minor": 1000,
                "currency": "USD",
                "description": "E2E test credit",
                "transaction_type": "grant",
                "idempotency_key": idempotency_key,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["amount_minor"] == 1000
        assert data["transaction_type"] == "grant"
        assert "balance_after" in data

    def test_add_credits_account_not_found(self, client):
        """Test adding credits to non-existent account."""
        response = client.post(
            "/v1/billing/credits",
            json={
                "oauth_provider": "oauth:google",
                "external_id": "nonexistent@example.com",
                "amount_minor": 1000,
                "currency": "USD",
                "description": "Test credit",
                "transaction_type": "purchase",
            },
        )
        assert response.status_code == 404


class TestValidation:
    """Test request validation."""

    def test_missing_required_field(self, client):
        """Test validation error for missing required field."""
        response = client.post(
            "/v1/billing/credits/check",
            json={
                "external_id": "test@example.com",
                "context": {},
            },
        )
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_invalid_oauth_provider(self, client):
        """Test validation error for invalid oauth_provider."""
        response = client.post(
            "/v1/billing/credits/check",
            json={
                "oauth_provider": "invalid-provider",  # Should start with "oauth:"
                "external_id": "test@example.com",
                "context": {},
            },
        )
        assert response.status_code == 422

    def test_negative_amount(self, client, test_user_1):
        """Test validation error for negative amount."""
        response = client.post(
            "/v1/billing/charges",
            json={
                **test_user_1,
                "amount_minor": -100,  # Negative amount
                "currency": "USD",
                "description": "Test",
                "metadata": {},
            },
        )
        assert response.status_code == 422
