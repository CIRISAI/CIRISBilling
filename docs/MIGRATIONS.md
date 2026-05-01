# Database Migrations on a Multi-Node Spock Cluster

CIRISBilling runs against a PostgreSQL deployment with **Spock 5.x logical
replication** between billing-jeanluc (US) and billing-spock (EU). This
document describes the contract migrations must follow to stay safe across
both nodes.

---

## The trap

Spock replicates **DML** on tables enrolled in a replication set. It does
**not** automatically replicate **DDL**.

Concretely: if a migration runs `op.create_table("foo", ...)` on the node
that wins the startup race, the table is created locally on that node only.
The `alembic_version` UPDATE that records the migration as applied **does**
replicate (because `alembic_version` is already in the `default` repset by
prior bootstrap). The peer node sees `alembic_version` advance, skips the
migration on its own startup, and silently runs without table `foo`.

We hit this in production with migrations `0018` (`admin_oauth_sessions`)
and `0019` (`stripe_payment_intents`) — see Issue #5.

---

## The contract

> **Every migration that creates, alters, or drops `public.*` schema MUST
> route its DDL through `spock.replicate_ddl()` when the Spock extension is
> present.**

In practice this means writing the DDL as a string and dispatching it via
the helper pattern below, not via `op.create_table()` / `op.create_index()`.

### Template

```python
"""Short description.

Revision ID: 20XX_XX_XX_NNNN
Revises: <previous>
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20XX_XX_XX_NNNN"
down_revision: str | None = "<previous>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DDL = """
CREATE TABLE IF NOT EXISTS my_new_table (
    id UUID PRIMARY KEY,
    ...
);
CREATE INDEX IF NOT EXISTS idx_my_new_table_xxx ON my_new_table (xxx);
"""


def _has_spock(bind: sa.engine.Connection) -> bool:
    return (
        bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'spock'")).scalar()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if _has_spock(bind):
        bind.execute(sa.text("SELECT spock.replicate_ddl(:ddl)").bindparams(ddl=_DDL))
    else:
        bind.execute(sa.text(_DDL))


def downgrade() -> None:
    bind = op.get_bind()
    drop_ddl = "DROP TABLE IF EXISTS my_new_table;"
    if _has_spock(bind):
        bind.execute(sa.text("SELECT spock.replicate_ddl(:ddl)").bindparams(ddl=drop_ddl))
    else:
        bind.execute(sa.text(drop_ddl))
```

### Why `IF NOT EXISTS`?

Two reasons:

1. **Recoverability.** If a migration partially ran on one peer (race won
   before this convention was followed), running the corrective migration
   later must be a no-op on that peer. Idempotent DDL keeps reconciliation
   migrations safe to run.
2. **Local resync.** When a peer is rebuilt from a snapshot or restored
   from backup, `IF NOT EXISTS` lets us re-run migrations against an
   already-populated schema without errors.

### Why the `_has_spock` detection?

Migrations also run in environments without Spock:

- **CI test job** (GitHub Actions) — single-node Postgres, no Spock extension.
- **Local development** — `docker-compose.local.yml` brings up a plain
  Postgres.
- **Fresh-install of a new region** before the Spock subscription is
  attached.

Without detection, every migration would fail in those environments with
`function spock.replicate_ddl(...) does not exist`.

### What about Alembic ORM helpers (`op.create_table`, `op.create_index`)?

They generate plain DDL via SQLAlchemy. **They do not route through
`spock.replicate_ddl()`**, so they trigger the bug this doc exists to
prevent. Use the SQL-string + dispatch pattern above instead.

If you need ORM-style metadata (e.g., for autogenerate diff support),
declare the model in `app/db/models.py` as usual — the migration just
ships the equivalent raw DDL.

---

## What enforced this on existing migrations

- `0018_add_admin_oauth_sessions.py` and `0019_add_stripe_payment_intents.py`
  predate this convention. They left peers out of sync in production.
- `0020_spock_replicate_oauth_and_payment_intent_tables.py` is the
  retroactive corrective migration: idempotent `CREATE TABLE IF NOT EXISTS`
  via `spock.replicate_ddl()`, no-op on the peer that already has the
  tables, creates them on the peer that doesn't.

Pre-existing migrations from before Spock was deployed (`0001` through
`0017`) are not retroactively wrapped. Their tables either already exist
on both nodes (because they pre-date the multi-node split) or were
explicitly enrolled by the bridge-side reconciliation pass.

---

## Bridge-side reconciliation (defense in depth)

`cirisai/cirisbridge` runs a post-deploy reconciliation task that scans
`pg_tables LEFT JOIN spock.tables` for any `public.*` table missing from
the `default` replication set, and enrolls it via
`spock.repset_add_table(..., synchronize_data=true)`.

That task remains valuable as a safety net — it catches drift from migrations
that didn't follow this convention, manual DDL, or other paths that bypass
alembic. **It is not a substitute for following the convention.** The
convention is the contract; the bridge reconciliation is the seatbelt.

---

## Operational checks

### Verify `alembic_version` matches across nodes

```sql
-- Should be identical on both nodes:
SELECT version_num FROM alembic_version;
```

### Verify all tables exist on both nodes

```sql
-- Run on each node; output should match:
SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
```

### Verify all `public.*` tables are enrolled in the `default` repset

```sql
SELECT t.tablename
FROM pg_tables t
LEFT JOIN spock.tables s
    ON s.relid = (t.schemaname || '.' || t.tablename)::regclass
   AND s.set_name = 'default'
WHERE t.schemaname = 'public'
  AND s.relid IS NULL
ORDER BY t.tablename;
-- Empty result = all tables enrolled.
```

If any of these checks return drift, run the bridge reconciliation task
or write a corrective migration following the template above.
