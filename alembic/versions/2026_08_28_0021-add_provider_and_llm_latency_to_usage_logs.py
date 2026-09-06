"""Add providers_used + llm_duration_ms to llm_usage_logs.

Revision ID: 2026_08_28_0021
Revises: 2026_05_01_0020
Create Date: 2026-08-28

Two blind spots this closes, both found while diagnosing a user-visible
latency incident on 2026-08-28:

1. `providers_used` — we record `models_used` but not WHICH upstream
   provider served the call. On OpenRouter a single model id
   (qwen/qwen3.6-35b-a3b) is served by ~10 different providers whose
   throughput spans 22-170 tok/s — a 7.7x spread. Without attribution,
   "the model is slow" cannot be narrowed to "this provider is slow",
   and the only way to find the culprit is to benchmark every provider
   by hand out-of-band, which is what we had to do.

2. `llm_duration_ms` — the existing `duration_ms` is measured from
   interaction START to FINALIZATION, and interactions are finalized on
   a stale timeout (5 min) or a call-limit trip, NOT when the last LLM
   call returns. It therefore includes agent-side processing and idle
   time between calls. A 24-minute `duration_ms` row prompted a hunt for
   a violated `request_timeout: 120` that was never actually violated.
   `llm_duration_ms` is the summed wall-clock of the LLM calls only, so
   the two can be compared and neither is mistaken for the other.

Both columns are nullable / defaulted so pre-existing rows stay valid and
an older proxy that does not yet send them keeps working.

Per docs/MIGRATIONS.md and Issue #5: all schema-changing DDL on a
multi-node Spock cluster MUST go through spock.replicate_ddl() or peer
nodes silently advance alembic_version without applying the DDL.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_08_28_0021"
down_revision: str | None = "2026_05_01_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# IF NOT EXISTS keeps this idempotent and safe to re-run on a node that
# already received the DDL via replication.
_UPGRADE_DDL = """
ALTER TABLE llm_usage_logs
    ADD COLUMN IF NOT EXISTS providers_used VARCHAR[] NOT NULL DEFAULT '{}';

ALTER TABLE llm_usage_logs
    ADD COLUMN IF NOT EXISTS llm_duration_ms INTEGER;
"""

_DOWNGRADE_DDL = """
ALTER TABLE llm_usage_logs DROP COLUMN IF EXISTS llm_duration_ms;
ALTER TABLE llm_usage_logs DROP COLUMN IF EXISTS providers_used;
"""


def _has_spock_extension(bind: sa.engine.Connection) -> bool:
    """Return True iff the `spock` extension is installed on this database."""
    return (
        bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'spock'")).scalar()
        is not None
    )


def _run(ddl: str) -> None:
    """Execute DDL, fanning it out via Spock when the extension is present."""
    bind = op.get_bind()
    if _has_spock_extension(bind):
        # Spock dispatches to peers over the ddl_sql replication set that
        # the existing subscriptions already listen on.
        bind.execute(sa.text("SELECT spock.replicate_ddl(:ddl)").bindparams(ddl=ddl))
    else:
        # Non-Spock environment (CI, local dev, single-node) — plain DDL.
        bind.execute(sa.text(ddl))


def upgrade() -> None:
    """Add provider attribution + true LLM latency columns."""
    _run(_UPGRADE_DDL)


def downgrade() -> None:
    """Drop both columns; symmetric to upgrade."""
    _run(_DOWNGRADE_DDL)
