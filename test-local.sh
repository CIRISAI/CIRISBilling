#!/bin/bash
# Local Testing Environment Setup
# Brings up complete stack with test data for end-to-end testing

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CIRIS Billing API - Local Testing Environment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}✗ docker-compose not found${NC}"
    echo "Please install Docker Compose"
    exit 1
fi

echo -e "${GREEN}✓${NC} Docker Compose found"

# Stop any existing containers
echo ""
echo "Cleaning up existing containers..."
docker-compose -f docker-compose.local.yml down -v 2>/dev/null || true

# Build the API image
echo ""
echo "Building API image..."
docker-compose -f docker-compose.local.yml build

# Start the stack
echo ""
echo "Starting services..."
docker-compose -f docker-compose.local.yml up -d

# Wait for database
echo ""
echo -e "${YELLOW}⏳${NC} Waiting for PostgreSQL..."
for i in {1..30}; do
    if docker-compose -f docker-compose.local.yml exec -T postgres pg_isready -U billing_admin -d ciris_billing > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} PostgreSQL is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}✗ PostgreSQL failed to start${NC}"
        exit 1
    fi
    sleep 1
done

# Run migrations
echo ""
echo "Running database migrations..."
docker-compose -f docker-compose.local.yml exec -T billing-api alembic upgrade head

# Wait for API
echo ""
echo -e "${YELLOW}⏳${NC} Waiting for API..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} API is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}✗ API failed to start${NC}"
        docker-compose -f docker-compose.local.yml logs billing-api
        exit 1
    fi
    sleep 1
done

# Load test data
echo ""
echo "Loading test data..."
docker-compose -f docker-compose.local.yml exec -T postgres psql -U billing_admin -d ciris_billing -f /docker-entrypoint-initdb.d/99-test-data.sql > /dev/null 2>&1

# Verify test data
echo ""
echo "Verifying test data..."
ACCOUNT_COUNT=$(docker-compose -f docker-compose.local.yml exec -T postgres psql -U billing_admin -d ciris_billing -t -c "SELECT COUNT(*) FROM accounts;" | tr -d '[:space:]')

if [ "$ACCOUNT_COUNT" -ge "5" ]; then
    echo -e "${GREEN}✓${NC} Test data loaded ($ACCOUNT_COUNT accounts)"
else
    echo -e "${YELLOW}⚠${NC} Expected at least 5 test accounts, found: $ACCOUNT_COUNT"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✓ Local testing environment is ready!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Services:"
echo "  API:        http://localhost:8000"
echo "  Metrics:    http://localhost:8000/metrics"
echo "  Health:     http://localhost:8000/health"
echo "  Docs:       http://localhost:8000/docs"
echo ""
echo "Observability:"
echo "  Grafana:    http://localhost:3000 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Jaeger:     http://localhost:16686"
echo ""
echo "Database:"
echo "  Host:       localhost:5432"
echo "  Database:   ciris_billing"
echo "  User:       billing_admin"
echo "  Password:   testpass123"
echo ""
echo "Test Accounts:"
echo "  test-user-1@example.com    (balance: 5000)"
echo "  test-user-2@example.com    (balance: 50 - low)"
echo "  discord-user-123456        (balance: 0)"
echo "  suspended-user@example.com (suspended)"
echo "  whale-user@example.com     (balance: 100000)"
echo ""
echo "Quick Commands:"
echo "  View logs:     docker-compose -f docker-compose.local.yml logs -f"
echo "  Stop:          docker-compose -f docker-compose.local.yml down"
echo "  Reset:         ./test-local.sh"
echo "  Run tests:     ./tests/e2e/run-tests.sh"
echo ""
echo "Example API Call:"
echo "  curl -X POST http://localhost:8000/v1/billing/credits/check \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"oauth_provider\":\"oauth:google\",\"external_id\":\"test-user-1@example.com\",\"context\":{}}'"
echo ""
