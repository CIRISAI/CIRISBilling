#!/bin/bash
# PostgreSQL Replica Setup Script
# Creates streaming replication from primary

set -e

echo "Setting up PostgreSQL replica..."

# Wait for primary to be ready
until pg_isready -h postgres-primary -p 5432 -U billing_admin; do
  echo "Waiting for primary database to be ready..."
  sleep 2
done

echo "Primary is ready. Setting up replication..."

# Remove existing data directory if exists
rm -rf "$PGDATA"/*

# Create base backup from primary
PGPASSWORD="${POSTGRES_REPLICATION_PASSWORD:-replicator123}" pg_basebackup \
    -h postgres-primary \
    -D "$PGDATA" \
    -U replicator \
    -v \
    -P \
    -X stream \
    -c fast

# Create standby signal file
touch "$PGDATA/standby.signal"

# Configure recovery settings
cat > "$PGDATA/postgresql.auto.conf" <<EOF
primary_conninfo = 'host=postgres-primary port=5432 user=replicator password=${POSTGRES_REPLICATION_PASSWORD:-replicator123}'
hot_standby = on
EOF

echo "PostgreSQL replica configured successfully"
