#!/bin/bash
#
# CIRIS Billing - Docker Secrets Setup Script
#
# This script sets up Docker secrets from environment variables.
# Run this on the server after deployment.
#
# Usage: ./scripts/setup-secrets.sh
#

set -e  # Exit on error

echo "============================================"
echo "CIRIS Billing - Docker Secrets Setup"
echo "============================================"
echo ""

# Configuration
SECRETS_DIR="./secrets"
BACKUP_DIR="./secrets-backup"

# Check if running in correct directory
if [ ! -f "docker-compose.admin.yml" ]; then
    echo "Error: Must run from /opt/ciris/billing directory"
    echo "Current directory: $(pwd)"
    exit 1
fi

# Create directories
echo "Creating secrets directories..."
mkdir -p "$SECRETS_DIR" "$BACKUP_DIR"
chmod 700 "$SECRETS_DIR" "$BACKUP_DIR"

# Function to extract secret from environment or backup
get_secret() {
    local secret_name="$1"
    local value=""

    # Try to get from running container first
    if docker ps | grep -q ciris-billing-api; then
        value=$(docker exec ciris-billing-api env 2>/dev/null | grep "^${secret_name}=" | cut -d= -f2- || true)
    fi

    # If not found, try backup file
    if [ -z "$value" ] && [ -f "$BACKUP_DIR/current-credentials.txt" ]; then
        value=$(grep "^${secret_name}=" "$BACKUP_DIR/current-credentials.txt" | cut -d= -f2- || true)
    fi

    # If still not found, try environment
    if [ -z "$value" ]; then
        value=$(printenv "$secret_name" || true)
    fi

    echo "$value"
}

# Backup current credentials if container is running
if docker ps | grep -q ciris-billing-api; then
    echo "Backing up current credentials..."
    docker exec ciris-billing-api env | grep -E "(DATABASE_URL|GOOGLE_CLIENT_SECRET|SECRET_KEY)" > "$BACKUP_DIR/current-credentials.txt" 2>/dev/null || true
    chmod 600 "$BACKUP_DIR/current-credentials.txt"
    echo "✓ Credentials backed up to $BACKUP_DIR/current-credentials.txt"
fi

# Extract secrets
echo ""
echo "Extracting secrets..."

DATABASE_URL=$(get_secret "DATABASE_URL")
GOOGLE_CLIENT_SECRET=$(get_secret "GOOGLE_CLIENT_SECRET")
SECRET_KEY=$(get_secret "SECRET_KEY")

# Validate secrets exist
if [ -z "$DATABASE_URL" ]; then
    echo "Error: DATABASE_URL not found in environment, container, or backup"
    exit 1
fi

if [ -z "$GOOGLE_CLIENT_SECRET" ]; then
    echo "Error: GOOGLE_CLIENT_SECRET not found in environment, container, or backup"
    exit 1
fi

if [ -z "$SECRET_KEY" ]; then
    echo "Error: SECRET_KEY not found in environment, container, or backup"
    exit 1
fi

echo "✓ Found DATABASE_URL"
echo "✓ Found GOOGLE_CLIENT_SECRET"
echo "✓ Found SECRET_KEY"

# Write secret files
echo ""
echo "Writing secret files..."

echo "$DATABASE_URL" > "$SECRETS_DIR/database_url.txt"
chmod 644 "$SECRETS_DIR/database_url.txt"
echo "✓ Created $SECRETS_DIR/database_url.txt"

echo "$GOOGLE_CLIENT_SECRET" > "$SECRETS_DIR/google_client_secret.txt"
chmod 644 "$SECRETS_DIR/google_client_secret.txt"
echo "✓ Created $SECRETS_DIR/google_client_secret.txt"

echo "$SECRET_KEY" > "$SECRETS_DIR/secret_key.txt"
chmod 644 "$SECRETS_DIR/secret_key.txt"
echo "✓ Created $SECRETS_DIR/secret_key.txt"

# Generate encryption key for future use (if doesn't exist)
if [ ! -f "$SECRETS_DIR/encryption_key.txt" ]; then
    echo ""
    echo "Generating encryption key for future use..."
    if command -v python3 &> /dev/null; then
        python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$SECRETS_DIR/encryption_key.txt"
        chmod 644 "$SECRETS_DIR/encryption_key.txt"
        echo "✓ Generated $SECRETS_DIR/encryption_key.txt"
    else
        echo "⚠ Python3 not available, skipping encryption key generation"
        echo "⚠ Run manually: python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > $SECRETS_DIR/encryption_key.txt"
    fi
else
    echo "✓ Using existing $SECRETS_DIR/encryption_key.txt"
fi

# Verify all files created
echo ""
echo "Verifying secret files..."
ls -lh "$SECRETS_DIR"

echo ""
echo "============================================"
echo "✓ Setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "1. Review docker-compose.admin.yml (should use secrets:)"
echo "2. Restart services: docker-compose -f docker-compose.admin.yml down && docker-compose -f docker-compose.admin.yml up -d"
echo "3. Verify: ./scripts/verify-secrets.sh"
echo ""
echo "Rollback if needed:"
echo "  cp docker-compose.admin.yml.backup-YYYYMMDD docker-compose.admin.yml"
echo "  docker-compose -f docker-compose.admin.yml down && docker-compose -f docker-compose.admin.yml up -d"
echo ""
