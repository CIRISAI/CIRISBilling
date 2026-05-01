"""Ensure admin_oauth_sessions + stripe_payment_intents exist on all Spock peers.

Revision ID: 2026_05_01_0020
Revises: 2026_05_01_0019
Create Date: 2026-05-01

Closes Issue #5: migrations 0018 and 0019 created their tables with raw
op.create_table() calls. On a multi-node Spock 5.x deploy, only the node
that won the start-up race created the tables; the alembic_version DML
replicated (because that table is in the `default` repset) but the
CREATE TABLE DDL did not. Peer nodes silently advanced alembic_version
without creating the tables.

This migration is idempotent and corrective:
- Wraps the DDL in `spock.replicate_ddl()` so it fans out via the
  `ddl_sql` replication set the existing subscriptions already listen on.
- Uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS so it is
  a no-op on the node where 0018/0019 already executed.
- Detects whether the `spock` extension is installed; falls back to plain
  DDL on non-Spock environments (CI, local dev, single-node deploys).

Convention going forward (see docs/MIGRATIONS.md):
all schema-changing DDL on a multi-node Spock cluster MUST go through
spock.replicate_ddl() to keep peers in sync.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_05_01_0020"
down_revision: str | None = "2026_05_01_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# DDL is the same on every node — Spock dispatches via the existing
# default subscription's ddl_sql replication set when wrapped.
_ENSURE_DDL = """
CREATE TABLE IF NOT EXISTS admin_oauth_sessions (
    state VARCHAR(64) PRIMARY KEY,
    redirect_uri VARCHAR(2048) NOT NULL,
    callback_url VARCHAR(2048) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_oauth_sessions_expires_at
    ON admin_oauth_sessions (expires_at);

CREATE TABLE IF NOT EXISTS stripe_payment_intents (
    payment_intent_id VARCHAR(255) PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    oauth_provider VARCHAR(255) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    amount_minor BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL,
    uses_purchased INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'created',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    credited_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_stripe_payment_intents_account_id
    ON stripe_payment_intents (account_id);

CREATE INDEX IF NOT EXISTS idx_stripe_payment_intents_status
    ON stripe_payment_intents (status);
"""


def _has_spock_extension(bind: sa.engine.Connection) -> bool:
    """Return True iff the `spock` extension is installed on this database."""
    return (
        bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'spock'")).scalar()
        is not None
    )


def upgrade() -> None:
    """Reconcile schema across all Spock peers; no-op on non-Spock DBs."""
    bind = op.get_bind()

    if _has_spock_extension(bind):
        # Dollar-quote DDL so it survives passing through spock.replicate_ddl
        # as a single string argument. Spock then dispatches it to peers
        # over the ddl_sql replication set.
        bind.execute(sa.text("SELECT spock.replicate_ddl(:ddl)").bindparams(ddl=_ENSURE_DDL))
    else:
        # Non-Spock environment (CI, local dev, single-node) — plain DDL.
        bind.execute(sa.text(_ENSURE_DDL))


def downgrade() -> None:
    """Drop the tables this migration ensures exist.

    Symmetric to the upgrade: replicate the DROP via Spock when present.
    """
    drop_ddl = """
    DROP INDEX IF EXISTS idx_stripe_payment_intents_status;
    DROP INDEX IF EXISTS idx_stripe_payment_intents_account_id;
    DROP TABLE IF EXISTS stripe_payment_intents;
    DROP INDEX IF EXISTS idx_admin_oauth_sessions_expires_at;
    DROP TABLE IF EXISTS admin_oauth_sessions;
    """

    bind = op.get_bind()
    if _has_spock_extension(bind):
        bind.execute(sa.text("SELECT spock.replicate_ddl(:ddl)").bindparams(ddl=drop_ddl))
    else:
        bind.execute(sa.text(drop_ddl))
