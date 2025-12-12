#!/bin/bash
# Apply S3 lifecycle policy to ciris-billing-archive bucket
# This is a simpler alternative to the CloudFormation template

set -e

BUCKET="ciris-billing-archive"

echo "Applying lifecycle policy to s3://${BUCKET}..."

aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --lifecycle-configuration '{
    "Rules": [
      {
        "ID": "BillingArchiveLifecycle",
        "Status": "Enabled",
        "Filter": {"Prefix": "billing-archive/"},
        "Transitions": [
          {"Days": 90, "StorageClass": "GLACIER_IR"},
          {"Days": 365, "StorageClass": "DEEP_ARCHIVE"}
        ],
        "Expiration": {"Days": 3650}
      }
    ]
  }'

echo "Lifecycle policy applied. Verifying..."
aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET"

echo "Done!"
