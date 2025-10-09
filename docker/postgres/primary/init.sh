#!/bin/bash
# PostgreSQL Primary Initialization Script
# Sets up replication user and configuration

set -e

echo "Configuring PostgreSQL primary for replication..."

# Create replication user
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create replication user
    CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD '${POSTGRES_REPLICATION_PASSWORD:-replicator123}';

    -- Grant connect privilege
    GRANT CONNECT ON DATABASE $POSTGRES_DB TO replicator;
EOSQL

# Update postgresql.conf for replication
cat >> "$PGDATA/postgresql.conf" <<EOF

# Replication settings
wal_level = replica
max_wal_senders = 3
max_replication_slots = 3
hot_standby = on
EOF

# Update pg_hba.conf to allow replication connections
cat >> "$PGDATA/pg_hba.conf" <<EOF

# Allow replication connections
host    replication    replicator    0.0.0.0/0    scram-sha-256
EOF

echo "PostgreSQL primary configured for replication"
