"""add llm usage logs table

Revision ID: 2025_11_25_0010
Revises: 2025_11_25_0009
Create Date: 2025-11-25 23:00:00.000000

Adds LLM usage logging for analytics and margin monitoring:
- llm_usage_logs: Track actual provider costs per interaction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2025_11_25_0010"
down_revision: str | None = "2025_11_25_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create llm_usage_logs table
    op.create_table(
        "llm_usage_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interaction_id", sa.String(length=255), nullable=False),
        sa.Column("charge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("total_llm_calls", sa.Integer(), nullable=False),
        sa.Column("total_prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column(
            "models_used",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("ARRAY[]::VARCHAR[]"),
        ),
        sa.Column("actual_cost_cents", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["charge_id"], ["charges.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("idx_llm_usage_logs_account_id", "llm_usage_logs", ["account_id"])
    op.create_index("idx_llm_usage_logs_interaction_id", "llm_usage_logs", ["interaction_id"])
    op.create_index("idx_llm_usage_logs_created_at", "llm_usage_logs", ["created_at"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_llm_usage_logs_created_at", table_name="llm_usage_logs")
    op.drop_index("idx_llm_usage_logs_interaction_id", table_name="llm_usage_logs")
    op.drop_index("idx_llm_usage_logs_account_id", table_name="llm_usage_logs")

    # Drop table
    op.drop_table("llm_usage_logs")
