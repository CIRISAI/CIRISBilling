"""add apple storekit support

Revision ID: 2026_01_29_0017
Revises: 2025_12_17_0016
Create Date: 2026-01-29 00:00:00.000000

Adds Apple StoreKit In-App Purchase support:
- apple_storekit_purchases: Purchase verification and idempotency tracking
- Updates provider_configs constraint to allow 'apple_storekit'
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_01_29_0017"
down_revision: str | None = "2025_12_17_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create apple_storekit_purchases table
    op.create_table(
        "apple_storekit_purchases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", sa.String(length=255), nullable=False),
        sa.Column("original_transaction_id", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.String(length=255), nullable=False),
        sa.Column("bundle_id", sa.String(length=255), nullable=False),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment", sa.String(length=50), nullable=False),
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
        "idx_apple_storekit_purchases_transaction_id",
        "apple_storekit_purchases",
        ["transaction_id"],
        unique=True,
    )
    op.create_index(
        "idx_apple_storekit_purchases_original_tx_id",
        "apple_storekit_purchases",
        ["original_transaction_id"],
    )
    op.create_index(
        "idx_apple_storekit_purchases_account_id",
        "apple_storekit_purchases",
        ["account_id"],
    )

    # Update provider_configs constraint to include 'apple_storekit'
    op.drop_constraint("ck_provider_configs_type", "provider_configs", type_="check")
    op.create_check_constraint(
        "ck_provider_configs_type",
        "provider_configs",
        "provider_type IN ('stripe', 'google_play', 'apple_storekit', 'square', 'paypal')",
    )


def downgrade() -> None:
    # Restore original provider_configs constraint
    op.drop_constraint("ck_provider_configs_type", "provider_configs", type_="check")
    op.create_check_constraint(
        "ck_provider_configs_type",
        "provider_configs",
        "provider_type IN ('stripe', 'google_play', 'square', 'paypal')",
    )

    # Drop apple_storekit_purchases table
    op.drop_index("idx_apple_storekit_purchases_account_id", table_name="apple_storekit_purchases")
    op.drop_index("idx_apple_storekit_purchases_original_tx_id", table_name="apple_storekit_purchases")
    op.drop_index("idx_apple_storekit_purchases_transaction_id", table_name="apple_storekit_purchases")
    op.drop_table("apple_storekit_purchases")
