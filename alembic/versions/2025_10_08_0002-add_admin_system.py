"""add admin system tables

Revision ID: 2025_10_08_0002
Revises: 2025_10_08_0001
Create Date: 2025-10-08 10:00:00.000000

Adds tables for admin UI:
- api_keys: Agent API key management
- admin_users: Admin authentication
- provider_configs: Payment provider configuration
- admin_audit_logs: Admin action audit trail
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_10_08_0002"
down_revision: str | None = "2025_10_08_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create api_keys table
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("environment", sa.String(length=10), nullable=False),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY['billing:read', 'billing:write']"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", postgresql.INET(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),  # type: ignore[no-untyped-call]
            nullable=False,
            server_default="{}",
        ),
        sa.CheckConstraint("environment IN ('test', 'live')", name="ck_api_keys_environment"),
        sa.CheckConstraint(
            "status IN ('active', 'rotating', 'revoked')", name="ck_api_keys_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_prefix"),
    )
    op.create_index("idx_api_keys_created_by", "api_keys", ["created_by"])
    op.create_index(
        "idx_api_keys_prefix_active",
        "api_keys",
        ["key_prefix"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("idx_api_keys_status", "api_keys", ["status"])

    # Create admin_users table
    op.create_table(
        "admin_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mfa_secret", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "role IN ('super_admin', 'admin', 'viewer')", name="ck_admin_users_role"
        ),
        sa.CheckConstraint(
            "email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}$'",
            name="ck_admin_users_email_format",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("idx_admin_users_email", "admin_users", ["email"])
    op.create_index("idx_admin_users_role", "admin_users", ["role"])

    # Create provider_configs table
    op.create_table(
        "provider_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),  # type: ignore[no-untyped-call]
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "provider_type IN ('stripe', 'square', 'paypal')", name="ck_provider_configs_type"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_type"),
    )

    # Create admin_audit_logs table
    op.create_table(
        "admin_audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),  # type: ignore[no-untyped-call]
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("idx_admin_audit_logs_admin_user", "admin_audit_logs", ["admin_user_id"])
    op.create_index(
        "idx_admin_audit_logs_created_at",
        "admin_audit_logs",
        ["created_at"],
        postgresql_using="brin",
    )
    op.create_index(
        "idx_admin_audit_logs_resource", "admin_audit_logs", ["resource_type", "resource_id"]
    )


def downgrade() -> None:
    # Drop all admin system tables
    op.drop_table("admin_audit_logs")
    op.drop_table("provider_configs")
    op.drop_table("admin_users")
    op.drop_table("api_keys")
