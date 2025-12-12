#!/bin/bash
# Setup script for CIRISBilling archive system
# Run this on the billing server after deploying CloudFormation

set -e

echo "=== CIRISBilling Archive Setup ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="/var/log/billing-archive.log"

echo "Project directory: $PROJECT_DIR"
echo ""

# Step 1: Install Python dependencies
echo "=== Installing Python dependencies ==="
pip3 install --quiet boto3 psycopg2-binary pyarrow pandas
echo "✓ Dependencies installed"
echo ""

# Step 2: Check AWS credentials
echo "=== Checking AWS credentials ==="
if [ -f ~/.aws/credentials ]; then
    echo "✓ AWS credentials file exists"
else
    echo "ERROR: AWS credentials not found at ~/.aws/credentials"
    echo ""
    echo "Create the file with:"
    echo "  mkdir -p ~/.aws"
    echo "  cat > ~/.aws/credentials << 'EOF'"
    echo "  [default]"
    echo "  aws_access_key_id = YOUR_ACCESS_KEY"
    echo "  aws_secret_access_key = YOUR_SECRET_KEY"
    echo "  region = us-east-1"
    echo "  EOF"
    echo ""
    exit 1
fi

# Test AWS access
echo "Testing S3 access..."
if aws s3 ls s3://ciris-billing-archive/ >/dev/null 2>&1; then
    echo "✓ S3 access verified"
else
    echo "ERROR: Cannot access s3://ciris-billing-archive/"
    echo "Check IAM permissions"
    exit 1
fi
echo ""

# Step 3: Set up log file
echo "=== Setting up logging ==="
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"
echo "✓ Log file: $LOG_FILE"
echo ""

# Step 4: Install cron job
echo "=== Installing cron job ==="
cp "$PROJECT_DIR/infrastructure/billing-archive.cron" /etc/cron.d/billing-archive
chmod 644 /etc/cron.d/billing-archive
echo "✓ Cron job installed (runs 2nd of month at 3 AM UTC)"
echo ""

# Step 5: Verify script is executable
echo "=== Verifying archive script ==="
chmod +x "$PROJECT_DIR/scripts/archive-billing.py"
echo "✓ Archive script: $PROJECT_DIR/scripts/archive-billing.py"
echo ""

# Step 6: Dry run test
echo "=== Running dry-run test ==="
echo "This will test the export without uploading..."
echo ""

# Get database URL from Docker volume
DB_URL=$(docker run --rm -v cirisbilling_database_url:/vol alpine cat /vol/database_url 2>/dev/null || echo "")

if [ -z "$DB_URL" ]; then
    echo "ERROR: Could not read DATABASE_URL from Docker volume"
    echo "Make sure the billing container is set up correctly"
    exit 1
fi

export DATABASE_URL="$DB_URL"

# Run dry test
if python3 "$PROJECT_DIR/scripts/archive-billing.py" --dry-run --verbose; then
    echo ""
    echo "✓ Dry run successful!"
else
    echo ""
    echo "ERROR: Dry run failed"
    exit 1
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Apply S3 lifecycle policy (if not done):"
echo "     aws s3api put-bucket-lifecycle-configuration --bucket ciris-billing-archive --cli-input-json file://$PROJECT_DIR/infrastructure/lifecycle-policy.json"
echo ""
echo "  2. Run a real archive (optional - for current/previous month):"
echo "     DATABASE_URL='$DB_URL' python3 $PROJECT_DIR/scripts/archive-billing.py"
echo ""
echo "  3. Monitor logs:"
echo "     tail -f $LOG_FILE"
echo ""
echo "  4. Verify in S3:"
echo "     aws s3 ls s3://ciris-billing-archive/billing-archive/ --recursive"
echo ""
