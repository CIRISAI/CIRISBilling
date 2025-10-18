#!/bin/bash
#
# CIRIS Billing - Verify Docker Secrets
#
# This script verifies that Docker secrets are properly configured.
#

set -e

echo "============================================"
echo "CIRIS Billing - Secrets Verification"
echo "============================================"
echo ""

# Check if container is running
if ! docker ps | grep -q ciris-billing-api; then
    echo "❌ Error: ciris-billing-api container not running"
    exit 1
fi

echo "✓ Container is running"
echo ""

# Test 1: Secrets should NOT be in environment
echo "Test 1: Checking environment variables are hidden..."
if docker exec ciris-billing-api env | grep -q "DATABASE_URL="; then
    echo "❌ FAIL: DATABASE_URL still in environment"
    FAIL=1
else
    echo "✓ PASS: DATABASE_URL not in environment"
fi

if docker exec ciris-billing-api env | grep -q "GOOGLE_CLIENT_SECRET="; then
    echo "❌ FAIL: GOOGLE_CLIENT_SECRET still in environment"
    FAIL=1
else
    echo "✓ PASS: GOOGLE_CLIENT_SECRET not in environment"
fi

if docker exec ciris-billing-api env | grep -E "^SECRET_KEY=" > /dev/null; then
    echo "❌ FAIL: SECRET_KEY still in environment"
    FAIL=1
else
    echo "✓ PASS: SECRET_KEY not in environment"
fi

echo ""

# Test 2: Secrets should be readable from /run/secrets/
echo "Test 2: Checking secrets are accessible via files..."
if docker exec ciris-billing-api test -f /run/secrets/database_url; then
    echo "✓ PASS: /run/secrets/database_url exists"
else
    echo "❌ FAIL: /run/secrets/database_url missing"
    FAIL=1
fi

if docker exec ciris-billing-api test -f /run/secrets/google_client_secret; then
    echo "✓ PASS: /run/secrets/google_client_secret exists"
else
    echo "❌ FAIL: /run/secrets/google_client_secret missing"
    FAIL=1
fi

if docker exec ciris-billing-api test -f /run/secrets/secret_key; then
    echo "✓ PASS: /run/secrets/secret_key exists"
else
    echo "❌ FAIL: /run/secrets/secret_key missing"
    FAIL=1
fi

echo ""

# Test 3: API health check
echo "Test 3: Checking API health..."
HEALTH_STATUS=$(curl -s https://billing.ciris.ai/health | grep -o '"status":"[^"]*"' | cut -d: -f2 | tr -d '"' || echo "error")

if [ "$HEALTH_STATUS" = "healthy" ]; then
    echo "✓ PASS: API is healthy"
else
    echo "❌ FAIL: API health check failed (status: $HEALTH_STATUS)"
    FAIL=1
fi

echo ""

# Test 4: Database connection
echo "Test 4: Checking database connection..."
DB_STATUS=$(curl -s https://billing.ciris.ai/health | grep -o '"database":"[^"]*"' | cut -d: -f2 | tr -d '"' || echo "error")

if [ "$DB_STATUS" = "connected" ]; then
    echo "✓ PASS: Database connected"
else
    echo "❌ FAIL: Database connection failed (status: $DB_STATUS)"
    FAIL=1
fi

echo ""
echo "============================================"

if [ -n "$FAIL" ]; then
    echo "❌ VERIFICATION FAILED"
    echo "============================================"
    echo ""
    echo "Rollback instructions:"
    echo "1. cd /opt/ciris/billing"
    echo "2. ls -1 docker-compose.admin.yml.backup-* | tail -1  # Find latest backup"
    echo "3. cp docker-compose.admin.yml.backup-YYYYMMDD docker-compose.admin.yml"
    echo "4. docker-compose -f docker-compose.admin.yml down"
    echo "5. docker-compose -f docker-compose.admin.yml up -d"
    exit 1
else
    echo "✓ ALL TESTS PASSED"
    echo "============================================"
    echo ""
    echo "Secrets are properly configured!"
    echo ""
    echo "Additional manual tests:"
    echo "1. Login to https://billing.ciris.ai (test OAuth)"
    echo "2. Test billing API with valid API key"
    echo "3. Monitor logs: docker-compose -f docker-compose.admin.yml logs -f billing-api"
    exit 0
fi
