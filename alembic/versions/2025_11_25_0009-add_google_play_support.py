"""add google play support

Revision ID: 2025_11_25_0009
Revises: 2025_10_21_0008
Create Date: 2025-11-25 00:00:00.000000

Adds Google Play In-App Billing support:
- google_play_purchases: Purchase verification and idempotency tracking
- Updates provider_configs constraint to allow 'google_play'
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_11_25_0009"
down_revision: str | None = "2025_10_21_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create google_play_purchases table
    op.create_table(
        "google_play_purchases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_token", sa.String(length=4096), nullable=False),
        sa.Column("order_id", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.String(length=255), nullable=False),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("purchase_time_millis", sa.BigInteger(), nullable=False),
        sa.Column("purchase_state", sa.Integer(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("credits_added", sa.Integer(), nullable=False),
        sa.Column("credit_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credit_id"], ["credits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for lookups
    op.create_index(
        "idx_google_play_purchases_purchase_token",
        "google_play_purchases",
        ["purchase_token"],
        unique=True,
    )
    op.create_index("idx_google_play_purchases_order_id", "google_play_purchases", ["order_id"])
    op.create_index("idx_google_play_purchases_account_id", "google_play_purchases", ["account_id"])

    # Update provider_configs constraint to include 'google_play'
    op.drop_constraint("ck_provider_configs_type", "provider_configs", type_="check")
    op.create_check_constraint(
        "ck_provider_configs_type",
        "provider_configs",
        "provider_type IN ('stripe', 'google_play', 'square', 'paypal')",
    )


def downgrade() -> None:
    # Restore original provider_configs constraint
    op.drop_constraint("ck_provider_configs_type", "provider_configs", type_="check")
    op.create_check_constraint(
        "ck_provider_configs_type",
        "provider_configs",
        "provider_type IN ('stripe', 'square', 'paypal')",
    )

    # Drop google_play_purchases table
    op.drop_index("idx_google_play_purchases_account_id", table_name="google_play_purchases")
    op.drop_index("idx_google_play_purchases_order_id", table_name="google_play_purchases")
    op.drop_index("idx_google_play_purchases_purchase_token", table_name="google_play_purchases")
    op.drop_table("google_play_purchases")
