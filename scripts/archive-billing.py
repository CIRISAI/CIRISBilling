#!/usr/bin/env python3
"""
CIRISBilling Monthly Archive to AWS S3

Exports billing data to Parquet format and uploads to S3 with Glacier lifecycle.
Designed for 10-year retention per EU AI Act and financial audit requirements.

Usage:
    # Archive previous month (default - for cron)
    python3 archive-billing.py

    # Archive specific month
    python3 archive-billing.py --year 2025 --month 11

    # Dry run (no upload)
    python3 archive-billing.py --dry-run

    # Verbose logging
    python3 archive-billing.py --verbose
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Third-party imports (install with: pip install boto3 psycopg2-binary pyarrow pandas)
try:
    import boto3
    import pandas as pd
    import psycopg2
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install boto3 psycopg2-binary pyarrow pandas")
    sys.exit(1)

# Configuration
S3_BUCKET = os.getenv("ARCHIVE_S3_BUCKET", "ciris-billing-archive")
S3_PREFIX = os.getenv("ARCHIVE_S3_PREFIX", "billing-archive")
S3_REGION = os.getenv("AWS_REGION", "us-east-1")

# Database connection - reads from Docker volume or environment
DATABASE_URL = os.getenv("DATABASE_URL")

# Tables to archive with their timestamp columns
ARCHIVE_TABLES = [
    ("credits", "created_at"),
    ("charges", "created_at"),
    ("google_play_purchases", "created_at"),
    ("llm_usage_logs", "created_at"),
    ("admin_audit_logs", "created_at"),
    ("credit_checks", "created_at"),
]

# Tables to snapshot (full copy each month)
SNAPSHOT_TABLES = ["accounts"]

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Get database URL from environment or Docker volume."""
    if DATABASE_URL:
        return DATABASE_URL

    # Try reading from Docker volume (production setup)
    volume_path = "/run/secrets/database_url"
    if os.path.exists(volume_path):
        with open(volume_path) as f:
            return f.read().strip()

    # Try reading from .env file
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip("'\"")

    raise OSError(
        "DATABASE_URL not found. Set DATABASE_URL environment variable "
        "or ensure /run/secrets/database_url exists."
    )


def get_db_connection():
    """Create database connection."""
    db_url = get_database_url()

    # Parse the URL
    # Format: postgresql://user:pass@host:port/dbname
    # Also handle: postgresql+asyncpg://user:pass@host:port/dbname
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url[22:]  # Remove "postgresql+asyncpg://"
    elif db_url.startswith("postgresql://"):
        db_url = db_url[13:]  # Remove "postgresql://"

    # Parse credentials and host
    if "@" in db_url:
        creds, hostpart = db_url.rsplit("@", 1)
        if ":" in creds:
            user, password = creds.split(":", 1)
        else:
            user, password = creds, ""
    else:
        user, password = "ciris", ""
        hostpart = db_url

    # Parse host and database
    if "/" in hostpart:
        hostport, dbname = hostpart.rsplit("/", 1)
    else:
        hostport, dbname = hostpart, "ciris_billing"

    if ":" in hostport:
        host, port = hostport.split(":")
        port = int(port)
    else:
        host, port = hostport, 5432

    logger.debug(f"Connecting to {host}:{port}/{dbname} as {user}")

    return psycopg2.connect(
        host=host,
        port=port,
        database=dbname,
        user=user,
        password=password,
    )


def export_table_for_month(
    conn, table: str, timestamp_col: str, year: int, month: int, output_dir: Path
) -> tuple[Path, int, str]:
    """
    Export a table's data for a specific month to Parquet.

    Returns: (file_path, record_count, checksum)
    """
    # Calculate date range
    start_date = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=UTC)

    query = f"""
        SELECT * FROM {table}
        WHERE {timestamp_col} >= %s AND {timestamp_col} < %s
        ORDER BY {timestamp_col}
    """

    logger.info(f"Exporting {table} for {year}-{month:02d}...")
    df = pd.read_sql(query, conn, params=[start_date, end_date])

    # Convert any UUID columns to strings for Parquet compatibility
    for col in df.columns:
        if df[col].dtype == object:
            try:
                # Check if it looks like UUIDs
                sample = df[col].dropna().head(1)
                if len(sample) > 0 and len(str(sample.iloc[0])) == 36:
                    df[col] = df[col].astype(str)
            except Exception:
                pass

    # Create output file
    filename = output_dir / f"{table}-{year}-{month:02d}.parquet.gz"
    df.to_parquet(filename, compression="gzip", index=False)

    # Calculate checksum
    checksum = hashlib.sha256(filename.read_bytes()).hexdigest()

    logger.info(f"  {table}: {len(df)} records, {filename.stat().st_size / 1024:.1f} KB")

    return filename, len(df), checksum


def export_snapshot(
    conn, table: str, year: int, month: int, output_dir: Path
) -> tuple[Path, int, str]:
    """
    Export full table snapshot.

    Returns: (file_path, record_count, checksum)
    """
    logger.info(f"Exporting {table} snapshot...")
    df = pd.read_sql(f"SELECT * FROM {table}", conn)

    # Convert UUIDs to strings
    for col in df.columns:
        if df[col].dtype == object:
            try:
                sample = df[col].dropna().head(1)
                if len(sample) > 0 and len(str(sample.iloc[0])) == 36:
                    df[col] = df[col].astype(str)
            except Exception:
                pass

    filename = output_dir / f"{table}-snapshot-{year}-{month:02d}.parquet.gz"
    df.to_parquet(filename, compression="gzip", index=False)

    checksum = hashlib.sha256(filename.read_bytes()).hexdigest()

    logger.info(f"  {table}: {len(df)} records (snapshot), {filename.stat().st_size / 1024:.1f} KB")

    return filename, len(df), checksum


def upload_to_s3(local_path: Path, year: int, month: int, dry_run: bool = False) -> str:
    """
    Upload file to S3 with Intelligent Tiering.

    Returns: S3 key
    """
    s3_key = f"{S3_PREFIX}/{year}/{month:02d}/{local_path.name}"

    if dry_run:
        logger.info(f"  [DRY RUN] Would upload to s3://{S3_BUCKET}/{s3_key}")
        return s3_key

    s3 = boto3.client("s3", region_name=S3_REGION)

    try:
        s3.upload_file(
            str(local_path),
            S3_BUCKET,
            s3_key,
            ExtraArgs={
                "StorageClass": "INTELLIGENT_TIERING",
                "ContentType": "application/octet-stream",
                "Metadata": {
                    "archive-version": "1.0",
                    "source": "ciris-billing",
                    "created-at": datetime.now(UTC).isoformat(),
                },
            },
        )
        logger.info(f"  Uploaded to s3://{S3_BUCKET}/{s3_key}")
    except ClientError as e:
        logger.error(f"Failed to upload {local_path.name}: {e}")
        raise

    return s3_key


def verify_s3_upload(s3_key: str, expected_checksum: str, dry_run: bool = False) -> bool:
    """Verify uploaded file exists and optionally check integrity."""
    if dry_run:
        return True

    s3 = boto3.client("s3", region_name=S3_REGION)

    try:
        response = s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.debug(f"  Verified {s3_key}: {response['ContentLength']} bytes")
        return True
    except ClientError as e:
        logger.error(f"Verification failed for {s3_key}: {e}")
        return False


def create_manifest(
    year: int, month: int, files: list[dict[str, Any]], start_time: datetime
) -> dict[str, Any]:
    """Create manifest file documenting the archive."""
    end_time = datetime.now(UTC)

    return {
        "archive_version": "1.0",
        "created_at": end_time.isoformat(),
        "period": f"{year}-{month:02d}",
        "period_start": datetime(year, month, 1, tzinfo=UTC).isoformat(),
        "period_end": (
            datetime(year + 1, 1, 1, tzinfo=UTC)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=UTC)
        ).isoformat(),
        "files": files,
        "total_records": sum(f["records"] for f in files),
        "total_files": len(files),
        "processing_seconds": (end_time - start_time).total_seconds(),
        "schema_version": "2025-12-11",
        "retention_years": 10,
        "compliance": ["EU_AI_Act", "Financial_Audit", "GDPR"],
        "bucket": S3_BUCKET,
        "prefix": f"{S3_PREFIX}/{year}/{month:02d}/",
    }


def archive_month(year: int, month: int, dry_run: bool = False) -> bool:
    """
    Archive a specific month's billing data.

    Returns: True if successful
    """
    start_time = datetime.now(UTC)
    logger.info("=" * 60)
    logger.info(f"Starting archive for {year}-{month:02d}")
    logger.info("=" * 60)

    # Create temp directory
    output_dir = Path(f"/tmp/billing-archive-{year}-{month:02d}")
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    success = True

    try:
        conn = get_db_connection()

        # Export time-series tables
        for table, ts_col in ARCHIVE_TABLES:
            try:
                path, count, checksum = export_table_for_month(
                    conn, table, ts_col, year, month, output_dir
                )
                s3_key = upload_to_s3(path, year, month, dry_run)

                if not verify_s3_upload(s3_key, checksum, dry_run):
                    success = False

                files.append(
                    {
                        "table": table,
                        "type": "time_series",
                        "records": count,
                        "s3_key": s3_key,
                        "checksum_sha256": checksum,
                        "size_bytes": path.stat().st_size,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to archive {table}: {e}")
                success = False

        # Export snapshots
        for table in SNAPSHOT_TABLES:
            try:
                path, count, checksum = export_snapshot(conn, table, year, month, output_dir)
                s3_key = upload_to_s3(path, year, month, dry_run)

                if not verify_s3_upload(s3_key, checksum, dry_run):
                    success = False

                files.append(
                    {
                        "table": f"{table}_snapshot",
                        "type": "snapshot",
                        "records": count,
                        "s3_key": s3_key,
                        "checksum_sha256": checksum,
                        "size_bytes": path.stat().st_size,
                    }
                )
            except Exception as e:
                logger.error(f"Failed to snapshot {table}: {e}")
                success = False

        conn.close()

        # Create and upload manifest
        manifest = create_manifest(year, month, files, start_time)
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        manifest_key = upload_to_s3(manifest_path, year, month, dry_run)
        logger.info(f"Manifest uploaded to s3://{S3_BUCKET}/{manifest_key}")

        # Summary
        total_records = sum(f["records"] for f in files)
        total_size = sum(f["size_bytes"] for f in files)
        duration = (datetime.now(UTC) - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info(f"Archive {'(DRY RUN) ' if dry_run else ''}complete for {year}-{month:02d}")
        logger.info(f"  Files: {len(files)}")
        logger.info(f"  Records: {total_records:,}")
        logger.info(f"  Size: {total_size / 1024 / 1024:.2f} MB")
        logger.info(f"  Duration: {duration:.1f} seconds")
        logger.info(f"  Location: s3://{S3_BUCKET}/{S3_PREFIX}/{year}/{month:02d}/")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Archive failed: {e}")
        success = False

    finally:
        # Cleanup temp files
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
            logger.debug(f"Cleaned up {output_dir}")

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Archive CIRISBilling data to S3 Glacier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Archive previous month (for cron jobs)
  python3 archive-billing.py

  # Archive specific month
  python3 archive-billing.py --year 2025 --month 11

  # Dry run (no uploads)
  python3 archive-billing.py --dry-run --verbose
        """,
    )
    parser.add_argument("--year", type=int, help="Year to archive (default: previous month)")
    parser.add_argument("--month", type=int, help="Month to archive (1-12)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Don't upload, just show what would happen"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine which month to archive
    if args.year and args.month:
        year, month = args.year, args.month
    else:
        # Default: archive previous month
        today = datetime.now(UTC)
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1

    # Validate month
    if not 1 <= month <= 12:
        logger.error(f"Invalid month: {month}")
        sys.exit(1)

    # Run archive
    success = archive_month(year, month, dry_run=args.dry_run)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
