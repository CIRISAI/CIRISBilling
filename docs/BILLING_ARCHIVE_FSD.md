# Billing Data Archive System - Functional Specification Document

**Created**: 2025-12-11
**Status**: Approved
**Author**: Claude Code (for Eric Moore)

---

## 1. Overview

### 1.1 Purpose
Implement automated monthly archival of CIRISBilling financial data to AWS S3 with Glacier Deep Archive lifecycle for 10-year retention, meeting EU AI Act and financial audit requirements.

### 1.2 Scope
- Monthly export of billing ledger tables to Parquet format
- Upload to AWS S3 with lifecycle tiering to Glacier Deep Archive
- Retention: 10 years (3650 days)
- No deletion from production DB (handled separately by operational retention)

---

## 2. Tables to Archive

### 2.1 Financial Ledger (REQUIRED - 10 years)

| Table | Records/Month (Est.) | Purpose |
|-------|---------------------|---------|
| `credits` | ~500 | Purchase/grant ledger |
| `charges` | ~10,000 | Usage deduction ledger |
| `google_play_purchases` | ~200 | Google Play IAP records |
| `llm_usage_logs` | ~10,000 | LLM cost tracking |

### 2.2 Audit Trail (REQUIRED - 10 years)

| Table | Records/Month (Est.) | Purpose |
|-------|---------------------|---------|
| `admin_audit_logs` | ~100 | Admin action audit |
| `credit_checks` | ~50,000 | Usage authorization audit |

### 2.3 Reference Data (Monthly Snapshot)

| Table | Records | Purpose |
|-------|---------|---------|
| `accounts` | ~1,000 total | Account state snapshot |

### 2.4 NOT Archived

| Table | Reason |
|-------|--------|
| `api_keys` | Operational, contains hashed secrets |
| `admin_users` | Operational, @ciris.ai only |
| `provider_configs` | Configuration, in git |
| `revoked_tokens` | Short-lived, auto-cleanup |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Billing Server (149.28.120.73)                                  │
│                                                                 │
│  ┌─────────────────┐    ┌─────────────────┐                    │
│  │ PostgreSQL      │───▶│ archive-billing │                    │
│  │ ciris_billing   │    │ Python script   │                    │
│  └─────────────────┘    └────────┬────────┘                    │
│                                  │                              │
│                                  ▼                              │
│                         ┌─────────────────┐                    │
│                         │ /tmp/billing-   │                    │
│                         │ archive-YYYY-MM │                    │
│                         │ *.parquet.gz    │                    │
│                         └────────┬────────┘                    │
│                                  │                              │
└──────────────────────────────────┼──────────────────────────────┘
                                   │ AWS CLI
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│ AWS S3: ciris-billing-archive                                   │
│                                                                 │
│  billing-archive/                                               │
│  ├── 2025/                                                      │
│  │   ├── 12/                                                    │
│  │   │   ├── credits-2025-12.parquet.gz                        │
│  │   │   ├── charges-2025-12.parquet.gz                        │
│  │   │   ├── google_play_purchases-2025-12.parquet.gz          │
│  │   │   ├── llm_usage_logs-2025-12.parquet.gz                 │
│  │   │   ├── admin_audit_logs-2025-12.parquet.gz               │
│  │   │   ├── credit_checks-2025-12.parquet.gz                  │
│  │   │   ├── accounts-snapshot-2025-12.parquet.gz              │
│  │   │   └── manifest.json                                      │
│  │   └── ...                                                    │
│  └── ...                                                        │
│                                                                 │
│  Lifecycle Policy:                                              │
│  ├── 0-90 days: S3 Standard / Intelligent Tiering              │
│  ├── 90-365 days: Glacier Instant Retrieval                    │
│  ├── 365-3650 days: Glacier Deep Archive                       │
│  └── 3650+ days: Expire (delete)                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Implementation

### 4.1 Script Location
```
/root/CIRISBilling/scripts/archive-billing.py
```

### 4.2 Dependencies
```
pip install boto3 psycopg2-binary pyarrow pandas
```

### 4.3 AWS Credentials
```bash
# /root/.aws/credentials (or environment variables)
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = ...

# IAM Policy: PutObject only to ciris-billing-archive/*
```

### 4.4 Cron Schedule
```bash
# /etc/cron.d/billing-archive
# Run at 3 AM UTC on the 2nd of each month (archive previous month)
0 3 2 * * root /usr/bin/python3 /root/CIRISBilling/scripts/archive-billing.py >> /var/log/billing-archive.log 2>&1
```

### 4.5 Script Pseudocode

```python
#!/usr/bin/env python3
"""
CIRISBilling Monthly Archive to AWS S3

Exports billing data to Parquet and uploads to S3 Glacier.
"""

import os
import json
import gzip
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

# Configuration
S3_BUCKET = "ciris-billing-archive"
S3_PREFIX = "billing-archive"
ARCHIVE_TABLES = [
    ("credits", "created_at"),
    ("charges", "created_at"),
    ("google_play_purchases", "created_at"),
    ("llm_usage_logs", "created_at"),
    ("admin_audit_logs", "created_at"),
    ("credit_checks", "created_at"),
]
SNAPSHOT_TABLES = ["accounts"]

def get_db_connection():
    """Connect to billing database."""
    return psycopg2.connect(
        host="localhost",
        port=5432,
        database="ciris_billing",
        user="ciris",
        password=os.environ["POSTGRES_PASSWORD"],
    )

def export_table_for_month(conn, table: str, timestamp_col: str, year: int, month: int) -> Path:
    """Export a table's data for a specific month to Parquet."""
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    query = f"""
        SELECT * FROM {table}
        WHERE {timestamp_col} >= %s AND {timestamp_col} < %s
        ORDER BY {timestamp_col}
    """

    df = pd.read_sql(query, conn, params=[start_date, end_date])

    output_path = Path(f"/tmp/billing-archive/{year}/{month:02d}")
    output_path.mkdir(parents=True, exist_ok=True)

    filename = output_path / f"{table}-{year}-{month:02d}.parquet.gz"
    df.to_parquet(filename, compression="gzip", index=False)

    return filename, len(df)

def export_snapshot(conn, table: str, year: int, month: int) -> Path:
    """Export full table snapshot."""
    df = pd.read_sql(f"SELECT * FROM {table}", conn)

    output_path = Path(f"/tmp/billing-archive/{year}/{month:02d}")
    output_path.mkdir(parents=True, exist_ok=True)

    filename = output_path / f"{table}-snapshot-{year}-{month:02d}.parquet.gz"
    df.to_parquet(filename, compression="gzip", index=False)

    return filename, len(df)

def upload_to_s3(local_path: Path, year: int, month: int):
    """Upload file to S3."""
    s3 = boto3.client("s3")
    s3_key = f"{S3_PREFIX}/{year}/{month:02d}/{local_path.name}"

    s3.upload_file(
        str(local_path),
        S3_BUCKET,
        s3_key,
        ExtraArgs={"StorageClass": "INTELLIGENT_TIERING"}
    )

    return s3_key

def create_manifest(year: int, month: int, files: list) -> dict:
    """Create manifest file documenting the archive."""
    return {
        "archive_version": "1.0",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "period": f"{year}-{month:02d}",
        "files": files,
        "schema_version": "2025-10-21",  # Last migration
        "retention_years": 10,
        "compliance": ["EU_AI_Act", "Financial_Audit"],
    }

def main():
    # Archive previous month
    today = datetime.utcnow()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    print(f"Archiving billing data for {year}-{month:02d}")

    conn = get_db_connection()
    files = []

    try:
        # Export time-series tables
        for table, ts_col in ARCHIVE_TABLES:
            path, count = export_table_for_month(conn, table, ts_col, year, month)
            s3_key = upload_to_s3(path, year, month)
            files.append({"table": table, "records": count, "s3_key": s3_key})
            print(f"  {table}: {count} records")

        # Export snapshots
        for table in SNAPSHOT_TABLES:
            path, count = export_snapshot(conn, table, year, month)
            s3_key = upload_to_s3(path, year, month)
            files.append({"table": f"{table}_snapshot", "records": count, "s3_key": s3_key})
            print(f"  {table} (snapshot): {count} records")

        # Create and upload manifest
        manifest = create_manifest(year, month, files)
        manifest_path = Path(f"/tmp/billing-archive/{year}/{month:02d}/manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2))
        upload_to_s3(manifest_path, year, month)

        print(f"Archive complete: {len(files)} files uploaded to s3://{S3_BUCKET}/{S3_PREFIX}/{year}/{month:02d}/")

    finally:
        conn.close()
        # Cleanup local files
        import shutil
        shutil.rmtree(f"/tmp/billing-archive/{year}", ignore_errors=True)

if __name__ == "__main__":
    main()
```

---

## 5. S3 Configuration

### 5.1 Bucket Settings
- **Name**: `ciris-billing-archive`
- **Region**: us-east-1 (or preferred)
- **Versioning**: Enabled
- **Object Lock**: Enabled (WORM compliance ready)

### 5.2 Lifecycle Policy
```json
{
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
}
```

### 5.3 IAM Policy (Minimal Permissions)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BillingArchiveWrite",
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::ciris-billing-archive/billing-archive/*"
    }
  ]
}
```

---

## 6. Data Recovery

### 6.1 Retrieval from Glacier Deep Archive
- **Restore time**: 12-48 hours
- **Restore command**:
```bash
aws s3api restore-object \
  --bucket ciris-billing-archive \
  --key billing-archive/2025/12/credits-2025-12.parquet.gz \
  --restore-request '{"Days": 7, "GlacierJobParameters": {"Tier": "Standard"}}'
```

### 6.2 Query with Athena (if needed)
Parquet format allows direct querying with AWS Athena without restoring from Glacier for recent data (before Deep Archive transition).

---

## 7. Monitoring

### 7.1 Success Verification
- Check `/var/log/billing-archive.log` for completion
- Verify S3 manifest file exists for each month
- CloudWatch alarm on S3 PutObject failures

### 7.2 Manual Verification
```bash
# List archived months
aws s3 ls s3://ciris-billing-archive/billing-archive/ --recursive | head -50

# Check specific month manifest
aws s3 cp s3://ciris-billing-archive/billing-archive/2025/12/manifest.json -
```

---

## 8. Compliance Mapping

| Requirement | Implementation |
|-------------|----------------|
| EU AI Act 10-year retention | S3 lifecycle expiration at 3650 days |
| Financial audit trail | Immutable ledger tables (credits, charges) |
| WORM compliance | S3 Object Lock enabled on bucket |
| Data integrity | Parquet checksums + manifest files |
| Encryption at rest | S3 default encryption (AES-256) |

---

## 9. Cost Estimate

| Item | Monthly Cost |
|------|-------------|
| S3 Intelligent Tiering (first 90 days) | ~$0.50 |
| Glacier IR (90-365 days) | ~$0.20 |
| Glacier Deep Archive (1-10 years) | ~$0.10 |
| **Total (10 years, ~100GB)** | **~$12/year** |

---

## 10. Implementation Checklist

- [ ] Create IAM user with minimal S3 permissions
- [ ] Store AWS credentials on billing server (`/root/.aws/credentials`)
- [ ] Install Python dependencies (`boto3 psycopg2-binary pyarrow pandas`)
- [ ] Deploy `scripts/archive-billing.py`
- [ ] Apply S3 lifecycle policy to bucket
- [ ] Add cron job (`/etc/cron.d/billing-archive`)
- [ ] Run manual test for current month
- [ ] Verify files in S3
- [ ] Set up CloudWatch alarm for failures

---

## 11. Appendix: Manifest Schema

```json
{
  "archive_version": "1.0",
  "created_at": "2025-12-02T03:00:00Z",
  "period": "2025-11",
  "files": [
    {"table": "credits", "records": 523, "s3_key": "billing-archive/2025/11/credits-2025-11.parquet.gz"},
    {"table": "charges", "records": 12847, "s3_key": "billing-archive/2025/11/charges-2025-11.parquet.gz"}
  ],
  "schema_version": "2025-10-21",
  "retention_years": 10,
  "compliance": ["EU_AI_Act", "Financial_Audit"]
}
```
