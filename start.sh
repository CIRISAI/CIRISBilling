#!/bin/bash
# Quick Start Script for CIRIS Billing API

set -e

echo "🚀 Starting CIRIS Billing API..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Copying from .env.example..."
    cp .env.example .env
    echo "✅ Created .env file. Please edit it with your passwords:"
    echo "   - DB_PASSWORD"
    echo "   - REPLICATION_PASSWORD"
    echo ""
    read -p "Press Enter after editing .env to continue..."
fi

# Start services
echo "📦 Starting Docker containers..."
docker-compose up -d

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 10

# Check if database is ready
echo "🔍 Checking database connection..."
until docker-compose exec -T postgres-primary pg_isready -U billing_admin -d ciris_billing > /dev/null 2>&1; do
    echo "   Waiting for PostgreSQL primary..."
    sleep 2
done
echo "✅ PostgreSQL primary is ready"

# Run migrations
echo "📊 Running database migrations..."
docker-compose exec -T billing-api-1 alembic upgrade head

echo ""
echo "✅ CIRIS Billing API is running!"
echo ""
echo "🔗 Endpoints:"
echo "   Load Balancer: http://localhost:8080"
echo "   Health Check:  http://localhost:8080/health"
echo "   API Docs:      http://localhost:8080/docs"
echo ""
echo "📊 Database:"
echo "   Primary:       localhost:5432"
echo "   Replica:       localhost:5433"
echo "   PgBouncer:     localhost:6432"
echo ""
echo "🛠️  Management:"
echo "   View logs:     docker-compose logs -f"
echo "   Stop services: docker-compose down"
echo "   Run tests:     docker-compose exec billing-api-1 pytest"
echo ""
