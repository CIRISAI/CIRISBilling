#!/bin/bash
# CIRIS Billing - Server Setup Script
# Run this ON THE SERVER after uploading code

set -e  # Exit on error

echo "===================================="
echo "CIRIS Billing - Server Setup"
echo "===================================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Step 1: Installing dependencies...${NC}"
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
else
    echo "Docker already installed"
fi

if ! command -v docker-compose &> /dev/null; then
    echo "Installing Docker Compose..."
    apt install -y docker-compose
else
    echo "Docker Compose already installed"
fi

if ! command -v certbot &> /dev/null; then
    echo "Installing Certbot..."
    apt install -y certbot
else
    echo "Certbot already installed"
fi

echo -e "${GREEN}✓ Dependencies installed${NC}"

# ============================================================================

echo -e "\n${YELLOW}Step 2: Extracting Google OAuth credentials...${NC}"

# Check if credentials file exists
if [ -f ~/client_secret_*.json ]; then
    CREDS_FILE=$(ls ~/client_secret_*.json)
    echo "Found credentials file: $CREDS_FILE"

    # Extract client ID and secret using Python
    CLIENT_ID=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['web']['client_id'])")
    CLIENT_SECRET=$(python3 -c "import json; print(json.load(open('$CREDS_FILE'))['web']['client_secret'])")

    echo "Client ID: ${CLIENT_ID:0:20}..."
    echo "Client Secret: ${CLIENT_SECRET:0:10}..."
else
    echo "ERROR: Google OAuth credentials file not found in ~/"
    echo "Please upload it first with:"
    echo "  scp -i ~/.ssh/ciris_deploy /path/to/client_secret_*.json root@149.28.120.73:~/"
    exit 1
fi

echo -e "${GREEN}✓ Credentials extracted${NC}"

# ============================================================================

echo -e "\n${YELLOW}Step 3: Generating secrets...${NC}"

POSTGRES_PASSWORD=$(openssl rand -base64 32)
ADMIN_JWT_SECRET=$(openssl rand -hex 32)
GRAFANA_PASSWORD=$(openssl rand -base64 16)

echo "Generated secure passwords"
echo -e "${GREEN}✓ Secrets generated${NC}"

# ============================================================================

echo -e "\n${YELLOW}Step 4: Creating .env file...${NC}"

cd /opt/ciris/billing

cat > .env << EOF
# PostgreSQL
POSTGRES_USER=ciris
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=ciris_billing
DATABASE_URL=postgresql+asyncpg://ciris:${POSTGRES_PASSWORD}@postgres:5432/ciris_billing

# Google OAuth
GOOGLE_CLIENT_ID=${CLIENT_ID}
GOOGLE_CLIENT_SECRET=${CLIENT_SECRET}
ADMIN_JWT_SECRET=${ADMIN_JWT_SECRET}

# Stripe (test keys - update with real keys later)
STRIPE_API_KEY=sk_test_placeholder
STRIPE_WEBHOOK_SECRET=whsec_placeholder
STRIPE_PUBLISHABLE_KEY=pk_test_placeholder

# Observability
LOG_LEVEL=INFO
GRAFANA_PASSWORD=${GRAFANA_PASSWORD}

# Application
PYTHONUNBUFFERED=1
EOF

chmod 600 .env

echo -e "${GREEN}✓ .env file created${NC}"

# Save credentials to a secure backup file
cat > ~/.ciris_credentials.txt << EOF
CIRIS Billing Credentials
=========================
Generated: $(date)

PostgreSQL Password: ${POSTGRES_PASSWORD}
Admin JWT Secret: ${ADMIN_JWT_SECRET}
Grafana Password: ${GRAFANA_PASSWORD}

Google OAuth:
  Client ID: ${CLIENT_ID}
  Client Secret: ${CLIENT_SECRET}

IMPORTANT: Keep this file secure!
EOF

chmod 600 ~/.ciris_credentials.txt

echo -e "${YELLOW}Credentials saved to: ~/.ciris_credentials.txt${NC}"

# ============================================================================

echo -e "\n${YELLOW}Step 5: Checking DNS...${NC}"

if dig +short billing.ciris.ai | grep -q "149.28.120.73"; then
    echo -e "${GREEN}✓ DNS is configured correctly${NC}"
    DNS_READY=true
else
    echo -e "${YELLOW}⚠ DNS not yet pointing to this server${NC}"
    echo "Current DNS: $(dig +short billing.ciris.ai)"
    echo "Expected: 149.28.120.73"
    echo ""
    echo "Please set DNS A record: billing.ciris.ai → 149.28.120.73"
    echo "Then run: certbot certonly --standalone -d billing.ciris.ai --email eric@ciris.ai --agree-tos"
    DNS_READY=false
fi

# ============================================================================

echo -e "\n${YELLOW}Step 6: Getting SSL certificate...${NC}"

if [ "$DNS_READY" = true ]; then
    if [ ! -d "/etc/letsencrypt/live/billing.ciris.ai" ]; then
        echo "Obtaining SSL certificate..."
        certbot certonly --standalone -d billing.ciris.ai --email eric@ciris.ai --agree-tos --non-interactive
        echo -e "${GREEN}✓ SSL certificate obtained${NC}"
    else
        echo "SSL certificate already exists"
        echo -e "${GREEN}✓ SSL certificate ready${NC}"
    fi

    # Set up auto-renewal
    if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
        (crontab -l 2>/dev/null; echo "0 0 * * 0 certbot renew --quiet && docker-compose -f /opt/ciris/billing/docker-compose.admin.yml restart nginx-admin") | crontab -
        echo -e "${GREEN}✓ SSL auto-renewal configured${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Skipping SSL certificate (DNS not ready)${NC}"
fi

# ============================================================================

echo -e "\n${YELLOW}Step 7: Starting services...${NC}"

cd /opt/ciris/billing

# Start postgres first
echo "Starting PostgreSQL..."
docker-compose -f docker-compose.admin.yml up -d postgres

# Wait for postgres to be ready
echo "Waiting for PostgreSQL to be ready..."
sleep 15

# Run migrations
echo "Running database migrations..."
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head

# Start all services
echo "Starting all services..."
docker-compose -f docker-compose.admin.yml up -d

echo -e "${GREEN}✓ All services started${NC}"

# ============================================================================

echo -e "\n${YELLOW}Step 8: Verifying deployment...${NC}"

sleep 5

# Check service status
echo "Service status:"
docker-compose -f docker-compose.admin.yml ps

# Check health endpoint
echo ""
echo "Testing health endpoint..."
if curl -f http://localhost/health 2>/dev/null; then
    echo -e "${GREEN}✓ Health check passed${NC}"
else
    echo -e "${YELLOW}⚠ Health check failed (may need a moment to start)${NC}"
fi

# ============================================================================

echo ""
echo "===================================="
echo -e "${GREEN}Setup Complete!${NC}"
echo "===================================="
echo ""
echo "Next steps:"
echo "1. View logs: docker-compose -f docker-compose.admin.yml logs -f"
echo "2. Access admin UI: https://billing.ciris.ai/admin"
echo "3. Login with: eric@ciris.ai"
echo "4. Create API key for CIRISAgent"
echo ""
echo "Credentials saved to: ~/.ciris_credentials.txt"
echo "View with: cat ~/.ciris_credentials.txt"
echo ""
echo "Useful commands:"
echo "  docker-compose -f docker-compose.admin.yml ps          # Status"
echo "  docker-compose -f docker-compose.admin.yml logs -f     # Logs"
echo "  docker-compose -f docker-compose.admin.yml restart     # Restart"
echo ""
