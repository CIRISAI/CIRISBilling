"""Add product_inventory and product_usage_logs tables for tool credits.

Revision ID: 2025_12_14_0015
Revises: 2025_12_05_0014
Create Date: 2025-12-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_12_14_0015"
down_revision: str | None = "2025_12_05_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create product_inventory and product_usage_logs tables."""
    # Product inventory - tracks credits per account per product type
    op.create_table(
        "product_inventory",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("free_remaining", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paid_credits", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_daily_refresh", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_uses", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint("free_remaining >= 0", name="ck_product_inventory_free_non_negative"),
        sa.CheckConstraint("paid_credits >= 0", name="ck_product_inventory_paid_non_negative"),
        sa.CheckConstraint("total_uses >= 0", name="ck_product_inventory_uses_non_negative"),
        sa.UniqueConstraint(
            "account_id", "product_type", name="uq_product_inventory_account_product"
        ),
    )
    op.create_index("idx_product_inventory_account_id", "product_inventory", ["account_id"])
    op.create_index("idx_product_inventory_product_type", "product_inventory", ["product_type"])

    # Product usage logs - immutable ledger of all tool usage
    op.create_table(
        "product_usage_logs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("used_free", sa.Boolean, nullable=False),
        sa.Column("used_paid", sa.Boolean, nullable=False),
        sa.Column("cost_minor", sa.Integer, nullable=False),
        sa.Column("free_before", sa.Integer, nullable=False),
        sa.Column("free_after", sa.Integer, nullable=False),
        sa.Column("paid_before", sa.Integer, nullable=False),
        sa.Column("paid_after", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_product_usage_idempotency"),
    )
    op.create_index("idx_product_usage_logs_account_id", "product_usage_logs", ["account_id"])
    op.create_index("idx_product_usage_logs_product_type", "product_usage_logs", ["product_type"])
    op.create_index("idx_product_usage_logs_created_at", "product_usage_logs", ["created_at"])
    op.create_index(
        "idx_product_usage_logs_idempotency",
        "product_usage_logs",
        ["idempotency_key"],
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop product_inventory and product_usage_logs tables."""
    # Drop product_usage_logs
    op.drop_index("idx_product_usage_logs_idempotency", table_name="product_usage_logs")
    op.drop_index("idx_product_usage_logs_created_at", table_name="product_usage_logs")
    op.drop_index("idx_product_usage_logs_product_type", table_name="product_usage_logs")
    op.drop_index("idx_product_usage_logs_account_id", table_name="product_usage_logs")
    op.drop_table("product_usage_logs")

    # Drop product_inventory
    op.drop_index("idx_product_inventory_product_type", table_name="product_inventory")
    op.drop_index("idx_product_inventory_account_id", table_name="product_inventory")
    op.drop_table("product_inventory")
