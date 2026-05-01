"""Add admin_oauth_sessions table for cross-worker OAuth state.

Revision ID: 2026_05_01_0018
Revises: 2026_01_29_0017
Create Date: 2026-05-01

Replaces the per-worker in-memory _sessions dict in AdminAuthService
with a Postgres-backed store so /admin/oauth/callback can land on a
different worker than /admin/oauth/login.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_05_01_0018"
down_revision: str | None = "2026_01_29_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create admin_oauth_sessions table."""
    op.create_table(
        "admin_oauth_sessions",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("callback_url", sa.String(2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_admin_oauth_sessions_expires_at",
        "admin_oauth_sessions",
        ["expires_at"],
    )


def downgrade() -> None:
    """Drop admin_oauth_sessions table."""
    op.drop_index(
        "idx_admin_oauth_sessions_expires_at",
        table_name="admin_oauth_sessions",
    )
    op.drop_table("admin_oauth_sessions")
