#!/bin/bash
# End-to-End API Tests
# Tests all major API endpoints against local stack

set -e

BASE_URL="http://localhost:8000"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0

# Helper function to run a test
run_test() {
    local test_name=$1
    local expected_status=$2
    local response=$3
    local status=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)

    if [[ "$status" = "$expected_status" ]]; then
        echo -e "${GREEN}✓${NC} $test_name"
        ((PASSED++))
        return 0
    else
        echo -e "${RED}✗${NC} $test_name"
        echo "  Expected: $expected_status, Got: $status"
        echo "  Response: $body"
        ((FAILED++))
        return 1
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CIRIS Billing API - End-to-End Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Test 1: Health Check
echo "Running health check..."
response=$(curl -s -w "\n%{http_code}" $BASE_URL/health)
run_test "Health check" "200" "$response"

# Test 2: Get existing account
echo ""
echo "Testing account retrieval..."
response=$(curl -s -w "\n%{http_code}" "$BASE_URL/v1/billing/accounts/oauth:google/test-user-1@example.com?wa_id=wa-test-001&tenant_id=tenant-acme")
run_test "Get existing account" "200" "$response"

# Test 3: Credit check - account with balance
echo ""
echo "Testing credit checks..."
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "wa_id": "wa-test-001",
    "tenant_id": "tenant-acme",
    "context": {}
  }')
run_test "Credit check - sufficient balance" "200" "$response"

# Test 4: Credit check - account with zero balance
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:discord",
    "external_id": "discord-user-123456",
    "context": {}
  }')
run_test "Credit check - zero balance" "200" "$response"

# Test 5: Credit check - suspended account
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "suspended-user@example.com",
    "wa_id": "wa-test-003",
    "tenant_id": "tenant-acme",
    "context": {}
  }')
run_test "Credit check - suspended account" "200" "$response"

# Test 6: Create a charge
echo ""
echo "Testing charge creation..."
IDEMPOTENCY_KEY="test-charge-$(date +%s)"
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:google\",
    \"external_id\": \"test-user-1@example.com\",
    \"wa_id\": \"wa-test-001\",
    \"tenant_id\": \"tenant-acme\",
    \"amount_minor\": 100,
    \"currency\": \"USD\",
    \"description\": \"E2E test charge\",
    \"idempotency_key\": \"$IDEMPOTENCY_KEY\",
    \"metadata\": {
      \"message_id\": \"e2e-test-msg\",
      \"agent_id\": \"test-agent\",
      \"request_id\": \"e2e-test-req\"
    }
  }")
run_test "Create charge - success" "201" "$response"

# Test 7: Idempotency - duplicate charge
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:google\",
    \"external_id\": \"test-user-1@example.com\",
    \"wa_id\": \"wa-test-001\",
    \"tenant_id\": \"tenant-acme\",
    \"amount_minor\": 100,
    \"currency\": \"USD\",
    \"description\": \"E2E test charge\",
    \"idempotency_key\": \"$IDEMPOTENCY_KEY\",
    \"metadata\": {}
  }")
run_test "Create charge - idempotency conflict" "409" "$response"

# Test 8: Charge - insufficient balance
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-2@example.com",
    "wa_id": "wa-test-002",
    "tenant_id": "tenant-acme",
    "amount_minor": 10000,
    "currency": "USD",
    "description": "Test charge - should fail",
    "metadata": {}
  }')
run_test "Create charge - insufficient balance" "402" "$response"

# Test 9: Charge - account not found
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/charges \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "nonexistent@example.com",
    "amount_minor": 100,
    "currency": "USD",
    "description": "Test charge",
    "metadata": {}
  }')
run_test "Create charge - account not found" "404" "$response"

# Test 10: Add credits
echo ""
echo "Testing credit addition..."
CREDIT_IDEMPOTENCY="test-credit-$(date +%s)"
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/credits \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:discord\",
    \"external_id\": \"discord-user-123456\",
    \"amount_minor\": 1000,
    \"currency\": \"USD\",
    \"description\": \"E2E test credit\",
    \"transaction_type\": \"grant\",
    \"idempotency_key\": \"$CREDIT_IDEMPOTENCY\"
  }")
run_test "Add credits - success" "201" "$response"

# Test 11: Create new account
echo ""
echo "Testing account creation..."
NEW_USER="e2e-test-user-$(date +%s)@example.com"
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d "{
    \"oauth_provider\": \"oauth:google\",
    \"external_id\": \"$NEW_USER\",
    \"initial_balance_minor\": 5000,
    \"currency\": \"USD\",
    \"plan_name\": \"test\"
  }")
run_test "Create new account" "201" "$response"

# Test 12: Upsert existing account
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "oauth_provider": "oauth:google",
    "external_id": "test-user-1@example.com",
    "wa_id": "wa-test-001",
    "tenant_id": "tenant-acme",
    "initial_balance_minor": 9999,
    "currency": "USD",
    "plan_name": "pro"
  }')
run_test "Upsert existing account" "201" "$response"

# Test 13: Get account - not found
response=$(curl -s -w "\n%{http_code}" "$BASE_URL/v1/billing/accounts/oauth:google/nonexistent@example.com")
run_test "Get account - not found" "404" "$response"

# Test 14: Invalid request - missing required field
echo ""
echo "Testing validation..."
response=$(curl -s -w "\n%{http_code}" -X POST $BASE_URL/v1/billing/credits/check \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "test@example.com",
    "context": {}
  }')
run_test "Validation - missing required field" "422" "$response"

# Test 15: Metrics endpoint
echo ""
echo "Testing observability..."
response=$(curl -s -w "\n%{http_code}" $BASE_URL/metrics)
run_test "Metrics endpoint" "200" "$response"

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Results"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Passed:${NC} $PASSED"
echo -e "  ${RED}Failed:${NC} $FAILED"
echo -e "  Total:  $(($PASSED + $FAILED))"
echo ""

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    echo ""
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    echo ""
    exit 1
fi
